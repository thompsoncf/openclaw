"""A logo do lojista chega inteira no documento.

Vem do orçamento de evento da Prime Eventos: a marca saiu no cabeçalho da folha
CORTADA e com um bloco PRETO atrás dela. Não era o cabeçalho — era o upload:

  • `aspecto=1.0` cortava a imagem no centro pra virar quadrado, comendo o topo
    e a base de uma marca alta (e as laterais de uma deitada);
  • tudo era convertido pra JPEG, que não tem canal alfa: o fundo transparente
    do PNG virava preto.

Aqui isso fica preso. O teste não sobe nada — exercita só o preparo da imagem
(`_redimensionar`), que é onde os dois estragos aconteciam.
"""
from __future__ import annotations

import io

import pytest

from finance import upload_foto

Image = pytest.importorskip("PIL.Image")


def _png_transparente(w: int, h: int) -> bytes:
    """Marca dourada num retângulo, com o resto transparente."""
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    im.paste((184, 134, 46, 255), (w // 4, h // 4, w - w // 4, h - h // 4))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _abrir(dados: bytes):
    return Image.open(io.BytesIO(dados))


def test_logo_alta_nao_e_cortada_em_quadrado():
    """Marca 474×800 (a da Prime Eventos) tem que sair 474×800, proporcional."""
    saida, ct = upload_foto._redimensionar(_png_transparente(474, 800),
                                           "image/png", manter_alpha=True)
    im = _abrir(saida)
    assert im.width / im.height == pytest.approx(474 / 800, rel=0.02)
    assert max(im.size) <= upload_foto._LADO_MAX


def test_logo_transparente_continua_transparente():
    """PNG com alfa sai PNG com alfa — JPEG transformava o transparente em preto."""
    saida, ct = upload_foto._redimensionar(_png_transparente(400, 700),
                                           "image/png", manter_alpha=True)
    assert ct == "image/png"
    im = _abrir(saida)
    assert im.mode == "RGBA"
    assert im.getpixel((1, 1))[3] == 0          # canto segue vazado


def test_sem_alpha_o_fundo_e_branco_e_nao_preto():
    """Quando o alfa é descartado (foto de produto), o fundo vira BRANCO.

    Papel e tela são brancos; achatar em preto é o que fazia a marca aparecer
    dentro de um bloco escuro."""
    saida, ct = upload_foto._redimensionar(_png_transparente(300, 300),
                                           "image/png")
    assert ct == "image/jpeg"
    im = _abrir(saida).convert("RGB")
    r, g, b = im.getpixel((1, 1))
    assert min(r, g, b) > 230


def test_capa_larga_ainda_corta_no_aspecto_pedido():
    """O corte não sumiu: quem pede aspecto (banner 7:1) continua sendo cortado."""
    saida, _ = upload_foto._redimensionar(_png_transparente(700, 700),
                                          "image/png", aspecto=7.0)
    im = _abrir(saida)
    assert im.width / im.height == pytest.approx(7.0, rel=0.02)


def test_foto_de_produto_segue_jpeg():
    """Nada de PNG pesado onde não precisa: foto sem transparência segue JPEG."""
    im = Image.new("RGB", (900, 600), (120, 90, 60))
    buf = io.BytesIO()
    im.save(buf, format="JPEG")
    saida, ct = upload_foto._redimensionar(buf.getvalue(), "image/jpeg",
                                           manter_alpha=True)
    assert ct == "image/jpeg"
    assert max(_abrir(saida).size) <= upload_foto._LADO_MAX
