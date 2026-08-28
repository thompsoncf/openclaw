"""A mídia buscada no CDN do WhatsApp, decifrada em fluxo, nunca guardada.

O QUE ESTE ARQUIVO PROTEGE
O arquivo não está no nosso disco: `mensagens.midia_ref` guarda o endereço no CDN e
a chave (migração 187). Se a derivação da chave sair errada por um byte, a foto vira
lixo binário — e o sintoma aparece só na tela do vendedor, no meio de uma conversa
com cliente. Então o formato inteiro é travado aqui, com ida e volta de verdade.

O FORMATO (Baileys, Utils/messages-media.js):

    chaves    = HKDF-SHA256(mediaKey, 112 bytes, info="WhatsApp <Tipo> Keys")
    iv        = chaves[0:16]
    cipherKey = chaves[16:48]
    corpo     = AES-256-CBC(cipherKey, iv) || mac[10]

A pegadinha é o `mac[10]` colado no fim: o Baileys nunca o remove explicitamente —
ele decifra só múltiplos de 16 e descarta o que sobra, e o que sobra é exatamente
ele. Quem não souber disso tropeça em "padding inválido" e culpa a chave.

E O CAMINHO É ENTRADA DE REDE
`directPath` vem do WhatsApp via wa-qr e é concatenado numa URL. Um valor como
`@servidor-de-alguem.com/x` faria `https://mmg.whatsapp.net` virar o USUÁRIO da URL
e o pedido sair pro servidor de outra pessoa. Isso tem teste próprio aqui embaixo, e
não é hipótese acadêmica: é a diferença entre buscar a foto e vazar cabeçalho.
"""
import os

import pytest

from finance import wa_midia as wm

TIPOS = ["imagem", "video", "documento", "figurinha"]


def _cifrar(claro: bytes, iv: bytes, ck: bytes) -> bytes:
    """Cifra como o WhatsApp cifra: PKCS7 + AES-256-CBC + mac[10] colado no fim."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    pad = 16 - (len(claro) % 16)
    enc = Cipher(algorithms.AES(ck), modes.CBC(iv)).encryptor()
    return enc.update(claro + bytes([pad]) * pad) + enc.finalize() + os.urandom(10)


def _pedacos(b: bytes, tam: int):
    return [b[i:i + tam] for i in range(0, len(b), tam)]


# ------------------------------------------------------------------- a chave

def test_o_hkdf_na_mao_bate_com_a_biblioteca():
    """A derivação é feita com `hmac` da biblioteca padrão pra ficar conferível em
    qualquer máquina. Se divergir da `cryptography`, a foto vira lixo."""
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives import hashes
    mk = os.urandom(32)
    iv, ck = wm.chaves(mk, "imagem")
    ref = HKDF(algorithm=hashes.SHA256(), length=112, salt=None,
               info=b"WhatsApp Image Keys").derive(mk)
    assert (iv, ck) == (ref[:16], ref[16:48])


def test_cada_tipo_tem_a_sua_string_de_info():
    """Trocar a info entre tipos dá chave diferente — e vídeo decifrado com chave de
    imagem não falha com erro, sai como ruído."""
    mk = os.urandom(32)
    vistas = {t: wm.chaves(mk, t) for t in TIPOS}
    assert vistas["imagem"] == vistas["figurinha"], \
        "figurinha deriva como imagem (MEDIA_HKDF_KEY_MAPPING do Baileys)"
    assert len({vistas["imagem"], vistas["video"], vistas["documento"]}) == 3


def test_tipo_desconhecido_e_chave_vazia_recusam():
    with pytest.raises(ValueError):
        wm.chaves(os.urandom(32), "holograma")
    with pytest.raises(ValueError):
        wm.chaves(b"", "imagem")


# --------------------------------------------------------------- ida e volta

@pytest.mark.parametrize("tam_pedaco", [7, 16, 1000, 1 << 20])
def test_decifra_igual_ao_original_em_qualquer_tamanho_de_pedaco(tam_pedaco):
    """A rede entrega pedaço de tamanho arbitrário — inclusive menor que um bloco."""
    mk = os.urandom(32)
    iv, ck = wm.chaves(mk, "imagem")
    claro = b"FOTO-DA-DECORACAO-" * 5000
    corpo = _cifrar(claro, iv, ck)
    assert b"".join(wm.decifrar(_pedacos(corpo, tam_pedaco), iv, ck)) == claro


@pytest.mark.parametrize("n", [0, 1, 15, 16, 17, 4096])
def test_tamanhos_de_borda(n):
    """Arquivo vazio, menor que um bloco, exatamente um bloco, e um a mais."""
    mk = os.urandom(32)
    iv, ck = wm.chaves(mk, "video")
    claro = os.urandom(n)
    assert b"".join(wm.decifrar(_pedacos(_cifrar(claro, iv, ck), 64), iv, ck)) == claro


def test_o_mac_do_fim_nao_vaza_pro_arquivo():
    """Os 10 bytes do fim não são conteúdo. Se vazarem, todo PDF sai corrompido e
    toda imagem ganha lixo no rodapé."""
    mk = os.urandom(32)
    iv, ck = wm.chaves(mk, "documento")
    claro = b"%PDF-1.7 conteudo do contrato"
    volta = b"".join(wm.decifrar(_pedacos(_cifrar(claro, iv, ck), 8), iv, ck))
    assert volta == claro and len(volta) == len(claro)


def test_nao_segura_o_arquivo_inteiro_na_memoria():
    """O ponto todo do desenho. O gerador tem que ir entregando enquanto lê — se
    juntasse tudo, um vídeo de 16 MB voltaria a ser 16 MB de pico por acesso."""
    mk = os.urandom(32)
    iv, ck = wm.chaves(mk, "video")
    corpo = _cifrar(os.urandom(400_000), iv, ck)
    lidos = []

    def fonte():
        for p in _pedacos(corpo, 4096):
            lidos.append(len(p))
            yield p

    g = wm.decifrar(fonte(), iv, ck)
    next(g)                       # só o primeiro pedaço de saída
    assert len(lidos) < 5, f"leu {len(lidos)} pedaços antes de entregar o primeiro"


# ------------------------------------------------------- o caminho é da rede

@pytest.mark.parametrize("mau", [
    "@servidor-de-alguem.com/x",      # vira o USUÁRIO da URL — o pedido sai pra fora
    "//servidor-de-alguem.com/x",     # protocolo-relativo, mesmo efeito
    "https://servidor-de-alguem.com/x",
    "v/t62/sem-barra-na-frente",
    "\\\\servidor\\x",
    "", "   ", None,
])
def test_caminho_que_leva_pra_fora_do_cdn_e_recusado(mau):
    with pytest.raises(ValueError):
        wm.url(mau)


def test_o_caminho_bom_vira_url_do_cdn():
    p = "/v/t62.7118-24/12345_67890_112233_n.enc"
    assert wm.url(p) == "https://mmg.whatsapp.net" + p


# ------------------------------------------------------------------- a rota

def test_a_rota_confere_a_conta_e_o_dono_do_lead():
    """O id da mensagem é sequencial e adivinhável. Sem o recorte, trocar um número
    na URL leria a foto do cliente de outra empresa — e, dentro da mesma empresa, a
    carteira do colega. Travado na FONTE porque o painel não tem harness de sessão
    (mesma limitação que tests/test_clientes_papel.py documenta)."""
    import inspect
    from web import painel_prospeccao as pp
    fonte = inspect.getsource(pp.prospeccao_midia)
    assert "join conversas cv on cv.id = m.conversa_id" in fonte
    assert "cv.conta_id=%s" in fonte
    assert "p.vendedor_id=%s" in fonte, "sem isto, vendedor lê a mídia do colega"
    assert "_acesso(request)" in fonte


def test_a_rota_separa_expirado_de_falha():
    """São recados diferentes na bolha: 'não está mais no servidor do WhatsApp' faz
    o vendedor pedir de novo ao cliente; 'não consegui carregar agora' faz ele
    tentar de novo. Trocar os dois manda ele fazer a coisa errada."""
    import inspect
    from web import painel_prospeccao as pp
    fonte = inspect.getsource(pp.prospeccao_midia)
    assert "except _wm.Expirou" in fonte
    assert "status_code=410" in fonte
    assert "status_code=502" in fonte


def test_a_rota_manda_cachear_no_navegador_e_so_nele():
    """É este cabeçalho que faz a segunda vez que a foto aparece não chegar no
    servidor. `private` porque é conversa de cliente: nunca num proxy compartilhado."""
    import inspect
    from web import painel_prospeccao as pp
    fonte = inspect.getsource(pp.prospeccao_midia)
    assert '"cache-control": "private, max-age=86400"' in fonte
    assert "StreamingResponse(" in fonte


def test_nome_de_arquivo_nao_quebra_o_cabecalho():
    from web.painel_prospeccao import _nome_seguro
    assert _nome_seguro('contrato".pdf\r\nX-Coisa: 1') == "contrato.pdfX-Coisa: 1"
    assert _nome_seguro("") == "arquivo"
    assert _nome_seguro(None) == "arquivo"
    assert len(_nome_seguro("a" * 300)) == 120
