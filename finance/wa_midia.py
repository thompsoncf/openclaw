"""A mídia do WhatsApp buscada na hora, sem nunca ser guardada.

O DESENHO
O WhatsApp já mantém a mídia cifrada no CDN dele. A mensagem que chega traz o
endereço (`directPath`) e a chave (`mediaKey`) — ~200 bytes que a migração 187
guarda em `mensagens.midia_ref`. Quando alguém abre a conversa, é aqui que se
busca: baixa do CDN, decifra em fluxo e repassa. O arquivo não encosta em disco
nenhum nosso e nunca existe inteiro na memória.

POR QUE NÃO GUARDAR
Medido em 28/08/2026 na Prime: 299 mensagens por dia com mídia. Guardar daria
~110 GB por ano numa conta só, contra 22 MB de ponteiro — e ainda pediria política
de retenção, disco e rotina de limpeza. O banco inteiro tem 87 MB. Fora que a
maioria das fotos ninguém abre: baixar todas seria pagar 100% da banda pelos 10%
que se olha.

POR QUE AQUI E NÃO NO wa-qr
`downloadContentFromMessage({mediaKey, directPath}, tipo)` do Baileys não pede o
objeto da mensagem nem o socket — só esses dois campos. Ou seja, a busca não
precisa do serviço que segura as sessões, e é melhor que não passe por ele: ele
tem 1 CPU e três sessões de WhatsApp na mão, e tráfego de vídeo ali é o caminho
mais curto pra derrubar canal de cliente.

O FORMATO, que o Baileys documenta implicitamente (Utils/messages-media.js):

    chaves      = HKDF-SHA256(mediaKey, 112 bytes, info="WhatsApp <Tipo> Keys")
    iv          = chaves[0:16]
    cipherKey   = chaves[16:48]
    macKey      = chaves[48:80]     (não usamos — ver decifrar)
    corpo       = AES-256-CBC(cipherKey, iv) || mac[10]

O `mac[10]` no fim é a pegadinha: o Baileys nunca o remove explicitamente. Ele
decifra só múltiplos de 16 e DESCARTA o resto que sobra no fim — e como o
ciphertext é múltiplo de 16, o que sobra são exatamente os 10 bytes do MAC. Aqui
a mesma coisa está escrita de propósito, porque "descarta o resto" só é óbvio
depois de alguém quebrar a cara com padding inválido.
"""
from __future__ import annotations

import hashlib
import hmac
from collections.abc import Iterable, Iterator

_HOST = "mmg.whatsapp.net"
# O CDN só entrega com esta origem — sem ela responde 403.
_ORIGEM = "https://web.whatsapp.com"

# A string de info do HKDF por tipo. Vem de MEDIA_HKDF_KEY_MAPPING (Baileys,
# Defaults/index.js): figurinha deriva como imagem, e é por isso que ela não tem
# entrada própria lá nem aqui.
_INFO = {
    "imagem": b"WhatsApp Image Keys",
    "figurinha": b"WhatsApp Image Keys",
    "video": b"WhatsApp Video Keys",
    "documento": b"WhatsApp Document Keys",
    "audio": b"WhatsApp Audio Keys",
}

# O MAC truncado que vem colado no fim do ciphertext.
_MAC = 10
_BLOCO = 16


def _hkdf(chave: bytes, tamanho: int, info: bytes) -> bytes:
    """HKDF-SHA256 com sal vazio, na mão.

    Na mão e não pela `cryptography` porque são oito linhas de `hmac` da biblioteca
    padrão, e assim esta parte — a que decide se a chave sai certa — fica
    conferível num teste que roda em qualquer máquina, sem depender de o binding
    nativo estar instalado.
    """
    prk = hmac.new(b"\x00" * hashlib.sha256().digest_size, chave, hashlib.sha256).digest()
    saida, bloco, i = b"", b"", 1
    while len(saida) < tamanho:
        bloco = hmac.new(prk, bloco + info + bytes([i]), hashlib.sha256).digest()
        saida += bloco
        i += 1
    return saida[:tamanho]


def chaves(media_key: bytes, tipo: str) -> tuple[bytes, bytes]:
    """(iv, cipher_key) para decifrar a mídia daquele tipo."""
    info = _INFO.get(tipo)
    if not info:
        raise ValueError(f"tipo de mídia sem derivação conhecida: {tipo!r}")
    if not media_key:
        raise ValueError("mediaKey vazia")
    x = _hkdf(media_key, 112, info)
    return x[:16], x[16:48]


def url(direct_path: str) -> str:
    """A URL no CDN — validando que o caminho não leva pra fora dele.

    ISTO NÃO É PARANOIA. `directPath` chega pela rede (o wa-qr repassa o que o
    WhatsApp mandou) e vai concatenado num endereço. Um valor como
    `@servidor-de-alguem.com/x` transformaria `https://mmg.whatsapp.net` no
    USUÁRIO da URL e o pedido sairia para o servidor de outra pessoa, levando junto
    o que estivesse nos cabeçalhos. `//outro.com/x` faz o mesmo pelo outro caminho.

    Então: tem que começar com uma barra, não pode começar com duas, e não pode ter
    arroba. Três comparações que fecham a porta.
    """
    p = (direct_path or "").strip()
    if not p.startswith("/") or p.startswith("//") or "@" in p or "\\" in p:
        raise ValueError("directPath fora do formato esperado")
    return f"https://{_HOST}{p}"


def decifrar(pedacos: Iterable[bytes], iv: bytes, cipher_key: bytes) -> Iterator[bytes]:
    """Decifra o fluxo em AES-256-CBC, devolvendo pedaço a pedaço.

    Duas coisas acontecem só no FIM e por isso o último bloco fica segurado:

    * o `mac[10]` colado no fim não é ciphertext — sai junto com o resto que não
      completa um bloco;
    * o padding PKCS7 mora no último bloco, e a gente só sabe qual é ele quando o
      fluxo acaba.

    A memória disto é o pedaço que a rede entregou mais um bloco. Não cresce com o
    tamanho do arquivo — é o ponto todo de fazer em fluxo.
    """
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    dec = Cipher(algorithms.AES(cipher_key), modes.CBC(iv)).decryptor()
    resto = b""        # ciphertext que ainda não fecha um bloco
    pendente = b""     # texto claro segurado até saber se é o último bloco
    for pedaco in pedacos:
        if not pedaco:
            continue
        dados = resto + pedaco
        n = (len(dados) // _BLOCO) * _BLOCO
        resto = dados[n:]
        if not n:
            continue
        pendente += dec.update(dados[:n])
        if len(pendente) > _BLOCO:
            yield pendente[:-_BLOCO]
            pendente = pendente[-_BLOCO:]
    # `resto` aqui são os 10 bytes do MAC: ciphertext é múltiplo de 16, então o que
    # sobra sem fechar bloco é exatamente ele. Descartado de propósito.
    pendente += dec.finalize()
    if pendente:
        yield _sem_padding(pendente)


def _sem_padding(bloco: bytes) -> bytes:
    """Tira o PKCS7 do último bloco — e devolve intacto se não parecer padding.

    Não levantar erro é decisão: a alternativa seria a foto inteira falhar por causa
    do último byte. Sobrar um byte de lixo no fim de um JPEG não estraga a imagem;
    não mostrar a foto estraga a conversa.
    """
    if not bloco:
        return bloco
    n = bloco[-1]
    if 1 <= n <= _BLOCO and len(bloco) >= n and bloco[-n:] == bytes([n]) * n:
        return bloco[:-n]
    return bloco


def buscar(ref: dict, tipo: str, *, timeout: float = 30.0) -> Iterator[bytes]:
    """Busca no CDN e devolve o conteúdo decifrado, em fluxo.

    Levanta `Expirou` quando o CDN responde 404/410 — que é o preço de não guardar
    o arquivo, e a tela precisa saber diferenciar isso de falha de rede pra dizer a
    coisa certa na bolha.
    """
    import httpx

    iv, ck = chaves(_bytes_da_chave(ref.get("mediaKey")), tipo)
    endereco = url(str(ref.get("directPath") or ""))
    with httpx.Client(timeout=timeout, follow_redirects=False) as c:
        with c.stream("GET", endereco, headers={"Origin": _ORIGEM}) as r:
            if r.status_code in (404, 410):
                raise Expirou(r.status_code)
            r.raise_for_status()
            yield from decifrar(r.iter_bytes(), iv, ck)


class Expirou(Exception):
    """O CDN do WhatsApp não tem mais o arquivo (404/410)."""


def _bytes_da_chave(v) -> bytes:
    """A mediaKey viaja em base64 (é Buffer no proto, e JSON não carrega Buffer)."""
    import base64
    if isinstance(v, (bytes, bytearray)):
        return bytes(v)
    try:
        return base64.b64decode(str(v or ""), validate=True)
    except Exception:  # noqa: BLE001
        return b""
