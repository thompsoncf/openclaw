"""Áudio de voz do vendedor: do navegador pro WhatsApp, sem recodificar nada.

O PROBLEMA
O navegador não grava no formato que o WhatsApp usa pra "áudio de voz". O
Chromium (Android e desktop) oferece `audio/webm;codecs=opus` e `audio/mp4`; o
Safari do iPhone oferece `audio/mp4`. O WhatsApp quer opus dentro de um container
Ogg pra desenhar a bolha com a onda.

O QUE FOI MEDIDO (19/08/2026, Chromium de verdade + as funções do próprio Baileys)
    manda webm  →  o Baileys lê a duração mas NÃO gera a onda ("Missing decoder
                   for webm format"). Bolha capenga.
    manda mp4   →  o Baileys não lê nem a duração. O cliente recebe 0:00.
    manda ogg   →  duração em 2 ms, onda com 64 pontos em 76 ms. Bolha completa.

A SAÍDA, E POR QUE ELA É BARATA
O webm do Chromium JÁ CARREGA OPUS (`A_OPUS` + `OpusHead`). Só a embalagem está
errada. Trocar a embalagem é copiar os mesmos quadros pra páginas Ogg — não é
recodificar. Cabe em stdlib, custa ~16 ms num áudio de 8s (~53 ms no áudio típico
de 27s do vendedor), e não perde uma amostra: conferido contra o `opusinfo` e
contra o decodificador do Chromium, que devolvem a mesma duração, as mesmas
380.160 amostras e a mesma energia.

O IPHONE não passa por aqui: AAC não é opus, e trocar embalagem não converte
codec. Ele é mandado como veio — o que funciona porque a TELA manda a duração e a
onda prontas, e aí o Baileys não precisa decodificar nada (a condição está no
código dele: `requiresDurationComputation = seconds === undefined`).
"""
from __future__ import annotations

import struct

# ------------------------------------------------------------------ EBML (WebM)

def _vint(b: bytes, i: int, com_marcador: bool):
    """Lê um número de tamanho variável do EBML. (valor, próximo_i)."""
    if i >= len(b) or b[i] == 0:
        return None, i + 1
    largura = 8 - b[i].bit_length() + 1
    if com_marcador:                       # IDs guardam o marcador de largura
        return int.from_bytes(b[i:i + largura], "big"), i + largura
    v = b[i] & ((1 << (8 - largura)) - 1)  # tamanhos, não
    for k in range(1, largura):
        v = (v << 8) | b[i + k]
    return v, i + largura


# elementos que CONTÊM outros: é neles que se desce
_RECIPIENTES = {0x18538067, 0x1654AE6B, 0xAE, 0xE1, 0x1F43B675, 0xA0}


def _extrair(b: bytes, fim=None, fora=None) -> dict:
    """Varre o WebM e recolhe o OpusHead e os quadros de áudio."""
    fora = fora if fora is not None else {"head": None, "pacotes": [], "lacing": 0}
    i, fim = 0, (fim if fim is not None else len(b))
    while i < fim - 1:
        eid, i = _vint(b, i, True)
        if eid is None:
            break
        tam, i = _vint(b, i, False)
        if tam is None:
            break
        tam = min(tam, fim - i)
        corpo = b[i:i + tam]
        if eid == 0x63A2 and corpo[:8] == b"OpusHead":       # CodecPrivate
            fora["head"] = corpo
        elif eid in (0xA3, 0xA1):                            # SimpleBlock / Block
            j = 0
            _, j = _vint(corpo, j, False)                    # número da faixa
            j += 2                                           # timecode relativo
            flags = corpo[j]
            j += 1
            if (flags >> 1) & 0b11:
                # lacing = vários quadros num bloco só. O Chromium não usa; se um
                # dia usar, é melhor recusar alto do que gerar um ogg embaralhado.
                fora["lacing"] += 1
            else:
                fora["pacotes"].append(corpo[j:])
        if eid in _RECIPIENTES:
            _extrair(corpo, len(corpo), fora)
        i += tam
    return fora


# ------------------------------------------------------------------ Ogg

def _crc(dados: bytes) -> int:
    """O CRC do Ogg: polinômio 0x04c11db7, SEM reflexão e SEM inversão final.

    NÃO é o `zlib.crc32` — o do zlib é refletido e invertido, e usar ele produz um
    arquivo que passa em qualquer conferência caseira e que nenhum tocador abre."""
    c = 0
    for by in dados:
        c ^= by << 24
        for _ in range(8):
            c = ((c << 1) ^ 0x04C11DB7) & 0xFFFFFFFF if c & 0x80000000 else (c << 1) & 0xFFFFFFFF
    return c


def _pagina(serial: int, seq: int, granulo: int, pacotes, bos=False, eos=False) -> bytes:
    segs = []
    for p in pacotes:
        n = len(p)
        while n >= 255:                    # pacote > 254 bytes ocupa vários
            segs.append(255)
            n -= 255
        segs.append(n)                     # o último < 255 fecha o pacote
    if len(segs) > 255:
        raise ValueError("página com mais de 255 segmentos")
    # header_type: 0x01 = pacote CONTINUADO, 0x02 = início do fluxo, 0x04 = fim.
    # Trocar 0x02 por 0x01 aqui gera um arquivo que o `opusinfo` recusa com
    # "no packet found" — aconteceu, e só uma implementação de fora pegou.
    cab = (b"OggS" + bytes([0]) + bytes([(2 if bos else 0) | (4 if eos else 0)])
           + struct.pack("<q", granulo) + struct.pack("<I", serial)
           + struct.pack("<I", seq) + b"\0\0\0\0" + bytes([len(segs)]) + bytes(segs))
    pg = cab + b"".join(pacotes)
    return pg[:22] + struct.pack("<I", _crc(pg)) + pg[26:]


def _amostras(pacote: bytes) -> int:
    """Duração do pacote opus em amostras de 48 kHz, lida do byte TOC (RFC 6716).

    É o que alimenta o granulepos das páginas — sem ele o tocador não sabe onde
    está no arquivo e a duração sai errada."""
    if not pacote:
        return 0
    toc = pacote[0]
    config, c = toc >> 3, toc & 0b11
    if config < 12:                        # SILK: 10/20/40/60 ms
        ms = (10, 20, 40, 60)[config % 4]
    elif config < 16:                      # híbrido: 10/20 ms
        ms = (10, 20)[config % 2]
    else:                                  # CELT: 2.5/5/10/20 ms
        ms = (2.5, 5, 10, 20)[config % 4]
    if c == 0:
        quadros = 1
    elif c in (1, 2):
        quadros = 2
    else:
        quadros = (pacote[1] & 0b111111) if len(pacote) > 1 else 1
    return int(48 * ms * max(1, quadros))


PACOTES_POR_PAGINA = 50        # ~1–3 s por página, que é o que os tocadores esperam


def webm_para_ogg(webm: bytes, serial: int = 0x5A415100) -> bytes:
    """Troca a embalagem: WebM/opus → Ogg/opus. Levanta ValueError se não der.

    Falhar alto é de propósito: quem chama decide se manda o original ou se avisa
    o vendedor. Gerar um ogg torto calado seria pior — o cliente receberia um
    áudio que não toca e ninguém saberia por quê."""
    d = _extrair(webm)
    if not d["head"]:
        raise ValueError("webm sem OpusHead: não é opus")
    if not d["pacotes"]:
        raise ValueError("webm sem quadros de áudio")
    if d["lacing"]:
        raise ValueError(f"webm com lacing em {d['lacing']} blocos: não tratado")
    tags = b"OpusTags" + struct.pack("<I", 3) + b"Zaq" + struct.pack("<I", 0)
    saida = [_pagina(serial, 0, 0, [d["head"]], bos=True),
             _pagina(serial, 1, 0, [tags])]
    seq, gran = 2, 0
    for k in range(0, len(d["pacotes"]), PACOTES_POR_PAGINA):
        lote = d["pacotes"][k:k + PACOTES_POR_PAGINA]
        gran += sum(_amostras(p) for p in lote)
        saida.append(_pagina(serial, seq, gran,
                             lote, eos=(k + PACOTES_POR_PAGINA) >= len(d["pacotes"])))
        seq += 1
    return b"".join(saida)


def duracao_segundos(webm: bytes) -> float:
    """Quanto tempo tem o áudio, lido dos próprios quadros. Serve de conferência
    contra o que a tela diz — a tela é a fonte, e ela erra."""
    d = _extrair(webm)
    return sum(_amostras(p) for p in d["pacotes"]) / 48000.0


# ------------------------------------------------------------------ o preparo

# 90 s cobre 98,5% dos áudios que o vendedor da Prime mandou nas últimas 5 semanas
# (511 de 519). O teto não é palpite: é o p90 medido, com folga.
LIMITE_SEGUNDOS = 90
# ~24 kbps de voz dão ~270 KB em 90 s. 1 MB deixa margem larga e ainda protege o
# serviço do WhatsApp, que roda com --max-old-space-size=320.
LIMITE_BYTES = 1024 * 1024

_OGG = "audio/ogg; codecs=opus"


def preparar(dados: bytes, mimetype: str) -> dict:
    """O que vai pro WhatsApp: {bytes, mimetype, convertido, erro?}.

    Webm vira ogg (bolha de voz completa). Qualquer outra coisa passa como veio —
    é o caso do iPhone, e funciona porque a tela manda duração e onda prontas.
    """
    tipo = (mimetype or "").split(";")[0].strip().lower()
    if tipo == "audio/webm":
        try:
            return {"bytes": webm_para_ogg(dados), "mimetype": _OGG, "convertido": True}
        except ValueError as e:
            # não trava o envio: manda o original e registra por quê. Áudio que
            # chega meio torto é melhor que áudio que não chega.
            return {"bytes": dados, "mimetype": mimetype, "convertido": False,
                    "erro": str(e)}
    return {"bytes": dados, "mimetype": mimetype or "audio/mp4", "convertido": False}
