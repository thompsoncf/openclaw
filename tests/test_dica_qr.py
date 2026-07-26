"""A dica de QR so' aparece pra CUPOM FISCAL (tem QR), nao pra comprovante de Pix.

Antes, qualquer foto/PDF (inclusive um comprovante de Pix) recebia a dica
"deixe o QR code do cupom visivel" - o que nao fazia sentido pra Pix (nao tem
QR). Agora a mensagem tem que bater com o que a pessoa enviou.
"""
from finance.nfce_qr import deve_mandar_dica_qr

CHAVE = "22260706022897000130650050003998681005896173"


def test_sem_dica_quando_flag_desligada():
    # texto puro / caso sem imagem: dica_qr=False -> nunca manda
    assert deve_mandar_dica_qr(False, None, set()) is False
    assert deve_mandar_dica_qr(False, CHAVE, {"registrar_itens_cupom"}) is False


def test_cupom_com_chave_lida_recebe_dica():
    assert deve_mandar_dica_qr(True, CHAVE, set()) is True


def test_cupom_com_itens_recebe_dica_mesmo_sem_chave():
    # QR ilegivel mas o agente registrou itens -> era cupom -> dica ajuda
    assert deve_mandar_dica_qr(True, None, {"lancar_despesa", "registrar_itens_cupom"}) is True


def test_comprovante_pix_nao_recebe_dica():
    # Pix: sem chave (nao tem QR) e sem itens (so' lancar_receita) -> SEM dica
    assert deve_mandar_dica_qr(True, None, {"checar_duplicata", "lancar_receita"}) is False
    assert deve_mandar_dica_qr(True, None, set()) is False


def test_tolera_tools_none():
    assert deve_mandar_dica_qr(True, None, None) is False
