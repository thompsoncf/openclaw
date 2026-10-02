"""A foto da obra também pelo Telegram (telegram_bot._midia_pro_livro).

Até 01/10/2026 só o webhook do WhatsApp deixava a imagem da mensagem em
`livro.midia_atual`; pelo Telegram, "foto do telhado da casa 3" chegava ao
agente, mas `guardar_foto_da_obra` respondia que não tinha foto.
"""
import base64
from types import SimpleNamespace

import telegram_bot as tb

FOTO = b"\xff\xd8\xff\xe0telhado"


def _agente():
    return SimpleNamespace(livro=SimpleNamespace(midia_atual=None))


def test_a_foto_vai_pro_livro():
    ag = _agente()
    tb._midia_pro_livro(ag, base64.b64encode(FOTO).decode(), "image/jpeg")
    assert ag.livro.midia_atual == (FOTO, "image/jpeg")


def test_pdf_e_texto_nao_viram_foto_de_obra():
    ag = _agente()
    tb._midia_pro_livro(ag, base64.b64encode(b"%PDF").decode(), "application/pdf")
    tb._midia_pro_livro(ag, None, "image/jpeg")
    assert ag.livro.midia_atual is None


def test_foto_que_nao_decodifica_nao_derruba():
    ag = _agente()
    tb._midia_pro_livro(ag, "isto não é base64!!", "image/jpeg")
    assert ag.livro.midia_atual is None
