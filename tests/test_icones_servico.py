"""O selo do item do orçamento é ÍCONE, e nunca falta.

Serviço não tem embalagem pra fotografar: metade dos itens ficava sem foto
("DJ", "mão de obra de entradas") e a linha do orçamento desalinhava. A escolha
é automática em três degraus — nome, categoria, 'outros' — e o vendedor pode
fixar um ícone no catálogo.
"""
from __future__ import annotations

import pytest

from finance import icones_servico as ics


@pytest.mark.parametrize("nome,esperado", [
    ("DJ", "som"),
    ("Som e iluminação de pista", "som"),
    ("LOCAÇÃO COZINHA", "cozinha"),
    ("Garçom", "equipe"),
    ("Garcom (sem acento)", "equipe"),
    ("Cadeira Modway Entreat", "moveis"),
    ("Bolo de casamento", "bolo"),
    ("Open bar", "bebidas"),
    ("Decoração da cerimônia", "decoracao"),
    ("Foto e vídeo", "foto"),
    ("Segurança noturna", "seguranca"),
    ("Van para os convidados", "transporte"),
])
def test_escolhe_pelo_nome(nome, esperado):
    assert ics.escolher(nome, "Outros") == esperado


def test_acento_e_caixa_nao_importam():
    """O lojista digita como quiser — "LOCAÇÃO COZINHA", "locacao cozinha"."""
    assert ics.escolher("LOCAÇÃO COZINHA") == ics.escolher("locacao cozinha") == "cozinha"


def test_cai_na_categoria_quando_o_nome_nao_diz_nada():
    assert ics.escolher("Item avulso 42", "Buffet") == "buffet"
    assert ics.escolher("Item avulso 42", "Serviços terceirizados") == "equipe"
    assert ics.escolher("Item avulso 42", "Locação de móveis e utensílios") == "moveis"


def test_nunca_volta_vazio():
    """Sem nome, sem categoria, com lixo: sempre sai um selo."""
    for n, c in [("", ""), (None, None), ("Coisa esquisita", "Categoria inventada")]:
        assert ics.escolher(n, c) in ics.ICONES


def test_o_que_o_vendedor_fixou_manda():
    assert ics.escolher("DJ", "Serviços terceirizados", fixo="bolo") == "bolo"
    # chave inválida (catálogo antigo, digitação): ignora e deduz
    assert ics.escolher("DJ", "Serviços terceirizados", fixo="nao-existe") == "som"


def test_svg_sai_pronto_e_sem_dependencia_externa():
    marcado = ics.svg("som", px=24)
    assert marcado.startswith("<svg") and marcado.endswith("</svg>")
    assert 'width="24"' in marcado and "currentColor" in marcado
    # nada de rede: o desenho é traço, não imagem
    assert "http" not in marcado and "<img" not in marcado


def test_paleta_traz_a_biblioteca_inteira():
    p = ics.paleta()
    assert len(p) == len(ics.ICONES)
    assert {x["chave"] for x in p} == set(ics.ICONES)
    assert all(x["svg"].startswith("<svg") and x["rotulo"] for x in p)
