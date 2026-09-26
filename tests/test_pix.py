"""O Pix copia e cola da própria empresa (finance/pix.py).

`test_crc_do_exemplo_do_bacen` — o exemplo do Manual de Padrões para Iniciação do
Pix, com o CRC publicado. Se o CRC sair errado, o app do banco recusa o código e
o cliente acha que a empresa mandou Pix quebrado.
"""
import re

import pytest

from finance import pix

_EXEMPLO = ("00020126580014br.gov.bcb.pix0136123e4567-e12b-12d1-a456-426655440000"
            "5204000053039865802BR5913Fulano de Tal6008BRASILIA62070503***6304")


def test_crc_do_exemplo_do_bacen():
    assert pix.crc16(_EXEMPLO) == "1D3D"


def _campos(payload: str) -> dict:
    """Lê o EMV de volta: id(2) + tamanho(2) + valor."""
    out, i = {}, 0
    while i < len(payload):
        k, n = payload[i:i + 2], int(payload[i + 2:i + 4])
        out[k] = payload[i + 4:i + 4 + n]
        i += 4 + n
    return out


def test_o_codigo_tem_chave_valor_nome_cidade_e_crc():
    p = pix.copia_e_cola("66.683.521/0001-07", 140_000, "PX2 Empreendimentos Construções Ltda",
                         "Lago da Pedra", txid="OBRA7-T31")
    c = _campos(p)
    assert c["00"] == "01" and c["52"] == "0000" and c["53"] == "986" and c["58"] == "BR"
    assert _campos(c["26"]) == {"00": "br.gov.bcb.pix", "01": "66683521000107"}
    assert c["54"] == "1400.00"
    assert c["59"] == "PX2 EMPREENDIMENTOS CONST" and len(c["59"]) <= 25   # sem acento, 25
    assert c["60"] == "LAGO DA PEDRA"
    assert _campos(c["62"]) == {"05": "OBRA7T31"}                            # só letra e número
    assert pix.crc16(p[:-4]) == p[-4:]


def test_sem_valor_e_sem_txid():
    c = _campos(pix.copia_e_cola("pablo@px2.com.br", None, "PX2", ""))
    assert "54" not in c and c["60"] == "BRASIL" and _campos(c["62"]) == {"05": "***"}


@pytest.mark.parametrize("entrada,tipo,chave", [
    ("Pablo@PX2.com.br", "email", "pablo@px2.com.br"),
    ("66.683.521/0001-07", "cnpj", "66683521000107"),
    ("529.982.247-25", "cpf", "52998224725"),
    ("(99) 98888-7777", "celular", "+5599988887777"),
    ("+55 99 98888-7777", "celular", "+5599988887777"),
    ("99988887777", "celular", "+5599988887777"),        # 11 dígitos que não fecham CPF
    ("123E4567-E12B-12D1-A456-426655440000", "aleatoria", "123e4567-e12b-12d1-a456-426655440000"),
])
def test_a_chave_do_jeito_que_a_pessoa_escreve(entrada, tipo, chave):
    assert pix.normalizar_chave(entrada) == (tipo, chave)


@pytest.mark.parametrize("entrada", ["", "66.683.521/0001-08", "pablo@", "123", "+1 555 1234"])
def test_chave_que_nao_fecha_explica(entrada):
    with pytest.raises(ValueError):
        pix.normalizar_chave(entrada)


def test_o_qr_sai_em_svg():
    svg = pix.qr_svg(pix.copia_e_cola("pablo@px2.com.br", 100, "PX2", "Lago da Pedra"))
    assert svg and svg.lstrip().startswith("<svg") and not re.search(r"<script", svg)
