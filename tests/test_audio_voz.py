"""A troca de embalagem do áudio de voz — webm/opus → ogg/opus.

POR QUE ESTES TESTES SÃO ASSIM
O material não é inventado: `tests/dados/voz_chromium.webm` e `.mp4` saíram de um
Chromium de verdade gravando com `MediaRecorder` a 24 kbps, que é exatamente o que
o celular do vendedor vai produzir. Áudio sintético não teria o `OpusHead` do
navegador nem os quadros de 60 ms que ele escolhe.

E a conferência é POR FORA sempre que dá. Escrever um validador com o meu próprio
CRC e perguntar a ele se o meu arquivo está certo é circular — o primeiro ogg que
eu gerei passava nessa conferência e o `opusinfo` recusava, porque eu tinha usado
0x01 (pacote continuado) no lugar de 0x02 (início do fluxo). Aqui as invariantes
são checadas contra o FORMATO, não contra a minha implementação.
"""
import struct
from pathlib import Path

import pytest

from finance import audio_voz as av

DADOS = Path(__file__).parent / "dados"
WEBM = (DADOS / "voz_chromium.webm").read_bytes()
MP4 = (DADOS / "voz_chromium.mp4").read_bytes()
DUR_GRAVADA = 7.92          # o que o Chromium entregou (8 s pedidos)


# ══════════════════════════════════════════════ o material é o que se espera

def test_o_webm_do_navegador_ja_carrega_opus():
    """É a descoberta que torna tudo barato: o navegador NÃO grava ogg, mas o que
    ele grava já é opus por dentro. Se um dia o Chromium mudar pra outro codec,
    este teste cai antes de a conversão começar a mentir."""
    d = av._extrair(WEBM)
    assert d["head"][:8] == b"OpusHead"
    assert d["pacotes"], "nenhum quadro de áudio"
    assert d["lacing"] == 0, "o Chromium passou a usar lacing — tratar"


def test_a_duracao_sai_dos_proprios_quadros():
    assert av.duracao_segundos(WEBM) == pytest.approx(DUR_GRAVADA, abs=0.05)


# ══════════════════════════════════════════════ o ogg gerado é um ogg de verdade

def _paginas(ogg: bytes):
    """Relê o arquivo pelo FORMATO (RFC 3533), sem usar nada do módulo."""
    fora, i = [], 0
    while i < len(ogg):
        assert ogg[i:i + 4] == b"OggS", f"página sem magia OggS em {i}"
        nsegs = ogg[i + 26]
        segs = ogg[i + 27:i + 27 + nsegs]
        tam = 27 + nsegs + sum(segs)
        fora.append({"versao": ogg[i + 4], "flags": ogg[i + 5],
                     "granulo": struct.unpack("<q", ogg[i + 6:i + 14])[0],
                     "serial": struct.unpack("<I", ogg[i + 14:i + 18])[0],
                     "seq": struct.unpack("<I", ogg[i + 18:i + 22])[0],
                     "crc": struct.unpack("<I", ogg[i + 22:i + 26])[0],
                     "segs": list(segs), "corpo": ogg[i + 27 + nsegs:i + tam],
                     "cru": ogg[i:i + tam]})
        i += tam
    return fora


def test_o_ogg_tem_as_duas_paginas_de_cabecalho_na_ordem():
    """OpusHead sozinho na primeira, OpusTags na segunda. É o que o `opusinfo`
    procura, e o que faltava quando o flag de início estava errado."""
    pgs = _paginas(av.webm_para_ogg(WEBM))
    assert pgs[0]["corpo"][:8] == b"OpusHead"
    assert len(pgs[0]["segs"]) == 1, "a página do OpusHead não pode levar carona"
    assert pgs[1]["corpo"][:8] == b"OpusTags"


def test_a_primeira_pagina_marca_inicio_de_fluxo_e_a_ultima_marca_fim():
    """0x02 é início, 0x04 é fim — e 0x01 (que eu usei por engano) é "pacote
    continuado". Com 0x01 o arquivo é recusado por qualquer tocador."""
    pgs = _paginas(av.webm_para_ogg(WEBM))
    assert pgs[0]["flags"] & 0x02, "faltou o marcador de início (BOS)"
    assert not pgs[0]["flags"] & 0x01, "a primeira página não pode ser continuação"
    assert pgs[-1]["flags"] & 0x04, "faltou o marcador de fim (EOS)"
    assert all(not p["flags"] & 0x01 for p in pgs), "nenhum pacote deve atravessar página"


def test_as_paginas_sao_numeradas_em_sequencia_e_do_mesmo_fluxo():
    pgs = _paginas(av.webm_para_ogg(WEBM))
    assert [p["seq"] for p in pgs] == list(range(len(pgs)))
    assert len({p["serial"] for p in pgs}) == 1
    assert all(p["versao"] == 0 for p in pgs)


def test_o_granulepos_cresce_e_fecha_na_duracao_certa():
    """O granulepos é o relógio do arquivo. Se ele não bater, a bolha do WhatsApp
    mostra uma duração e o áudio tem outra."""
    pgs = _paginas(av.webm_para_ogg(WEBM))
    audio = [p for p in pgs if p["granulo"] > 0]
    assert [p["granulo"] for p in audio] == sorted(p["granulo"] for p in audio)
    assert audio[-1]["granulo"] / 48000 == pytest.approx(DUR_GRAVADA, abs=0.05)


def test_o_crc_e_o_do_ogg_e_nao_o_do_zlib():
    """A armadilha silenciosa: `zlib.crc32` é o mesmo polinômio, mas refletido e
    invertido. Um arquivo com o CRC do zlib passa em qualquer conferência caseira
    e é recusado por todo tocador."""
    import zlib
    pgs = _paginas(av.webm_para_ogg(WEBM))
    p = pgs[0]
    zerado = bytearray(p["cru"])
    zerado[22:26] = b"\0\0\0\0"
    assert av._crc(bytes(zerado)) == p["crc"]
    assert zlib.crc32(bytes(zerado)) != p["crc"], "isso é o CRC do zlib, não o do Ogg"


def test_nenhuma_pagina_passa_de_255_segmentos():
    """Limite do formato. Estourar gera uma página inválida em silêncio."""
    assert all(len(p["segs"]) <= 255 for p in _paginas(av.webm_para_ogg(WEBM)))


def test_o_ultimo_segmento_de_cada_pacote_fecha_o_pacote():
    """Um pacote de tamanho múltiplo de 255 precisa de um segmento 0 no fim,
    senão o tocador acha que ele continua na página seguinte."""
    for p in _paginas(av.webm_para_ogg(WEBM)):
        if p["granulo"] <= 0:
            continue
        assert p["segs"][-1] != 255, "página termina em 255: pacote sem fechamento"


# ══════════════════════════════════════════════ não perde áudio

def test_a_troca_de_embalagem_preserva_todos_os_quadros():
    """Sem perda é o ponto: os mesmos bytes de áudio, em outra caixa. Se algum
    quadro sumisse, o cliente ouviria um pulo — e ninguém saberia dizer onde."""
    d = av._extrair(WEBM)
    ogg = av.webm_para_ogg(WEBM)
    audio = b"".join(p["corpo"] for p in _paginas(ogg) if p["granulo"] > 0)
    assert audio == b"".join(d["pacotes"])


def test_o_opus_head_do_navegador_vai_inteiro():
    """Copiado, não remontado: ele carrega pre-skip, ganho e mapeamento que só o
    codificador sabe. Reescrever seria chutar."""
    d = av._extrair(WEBM)
    assert _paginas(av.webm_para_ogg(WEBM))[0]["corpo"] == d["head"]


# ══════════════════════════════════════════════ o preparo, e o iPhone

def test_webm_e_convertido_pra_ogg():
    r = av.preparar(WEBM, "audio/webm;codecs=opus")
    assert r["convertido"] is True
    assert r["mimetype"] == "audio/ogg; codecs=opus"
    assert r["bytes"][:4] == b"OggS"


def test_mp4_do_iphone_passa_como_veio():
    """AAC não é opus — trocar a embalagem não converte codec. O mp4 vai inteiro,
    e funciona porque a TELA manda duração e onda prontas: o Baileys só chama os
    decodificadores quando falta informação (requiresDurationComputation)."""
    r = av.preparar(MP4, "audio/mp4")
    assert r["convertido"] is False
    assert r["bytes"] == MP4 and r["mimetype"] == "audio/mp4"


def test_webm_que_nao_e_opus_nao_derruba_o_envio():
    """Degrada em vez de travar: manda o original e diz por quê. Áudio que chega
    meio torto é melhor que áudio que não chega — e o erro fica no registro."""
    r = av.preparar(b"\x1a\x45\xdf\xa3" + b"\x00" * 64, "audio/webm")
    assert r["convertido"] is False and r["erro"]
    assert r["bytes"][:4] == b"\x1a\x45\xdf\xa3"


@pytest.mark.parametrize("lixo", [b"", b"nada disso", b"\x1a\x45\xdf\xa3"])
def test_lixo_levanta_em_vez_de_gerar_ogg_torto(lixo):
    with pytest.raises(ValueError):
        av.webm_para_ogg(lixo)


def test_os_tetos_saem_dos_dados_e_nao_de_chute():
    """90 s cobre 511 dos 519 áudios que o vendedor da Prime mandou em 5 semanas."""
    assert av.LIMITE_SEGUNDOS == 90
    assert av.LIMITE_BYTES == 1024 * 1024
    # o áudio típico (27 s a 24 kbps) cabe com folga larga
    assert len(WEBM) * (27 / 7.92) < av.LIMITE_BYTES / 4


def test_pacote_de_255_bytes_ganha_o_segmento_zero_que_o_fecha():
    """O caso que o material gravado não tem e que a produção vai ter.

    A tabela de segmentos diz o tamanho de cada pedaço; 255 significa "continua no
    próximo". Um pacote de exatamente 255 bytes precisa de um 0 depois, senão ele
    GRUDA no pacote seguinte e o tocador ouve os dois como um só — quadro
    embaralhado, sem erro nenhum aparecendo. Testado direto na montagem da página
    porque nenhum áudio de amostra cai nesse tamanho por acaso."""
    pg = av._pagina(1, 0, 960, [b"x" * 255, b"y" * 10])
    nsegs = pg[26]
    segs = list(pg[27:27 + nsegs])
    assert segs == [255, 0, 10], f"lacing errado: {segs}"
    # e o corpo continua sendo os dois pacotes, inteiros e na ordem
    assert pg[27 + nsegs:] == b"x" * 255 + b"y" * 10


def test_pacote_maior_que_255_e_partido_e_fechado():
    pg = av._pagina(1, 0, 960, [b"z" * 600])
    segs = list(pg[27:27 + pg[26]])
    assert segs == [255, 255, 90] and sum(segs) == 600


def test_o_mp4_nao_passa_pela_conversao_a_toa():
    """Mandar o mp4 pro conversor "por garantia" devolve o mesmo resultado — mas
    com um `erro` gravado em toda mensagem de iPhone, o que enche o log de falha
    que não é falha e esconde a que é."""
    r = av.preparar(MP4, "audio/mp4")
    assert "erro" not in r, "iPhone não pode gerar erro de conversão: é o caminho normal"
