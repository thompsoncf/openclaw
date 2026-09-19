"""A folha que o cliente lê, quando o item vem junto no pacote.

Até 19/09/2026 a linha "incluso" era impressa como desconto: o valor de tabela
riscado e "0,00" ao lado. O cliente lia um abatimento de R$ 4.000 que ninguém
deu, e o vendedor perdia a frase que ele de fato quer dizer — *isto já vem
junto*. Ver `finance.desconto.eh_incluso`: em 122 das 217 linhas da Prime o
campo de desconto estava sendo usado exatamente pra isso.

O que este arquivo fixa: a linha inclusa sai com a palavra **Incluso** e o valor
de tabela ao lado, sem tarja de riscado, e **o total não muda**.
"""
from __future__ import annotations

from finance import desconto as dsc
from web.proposta import _linhas_evento


def _linhas(itens):
    return _linhas_evento({"itens": itens})


def test_a_linha_inclusa_diz_incluso_e_nao_finge_desconto():
    l = _linhas([{"nome": "LOCAÇÃO MESA DE MADEIRA", "setup": 4000, "qtd": 20,
                  "unitario": 200, "desc_tipo": "pct", "desc_val": 100}])[0]
    assert l["incluso"] is True
    assert l["tabela"]                      # o valor de tabela continua à vista
    assert l["cheio"] == ""                 # sem riscado: riscar é dizer desconto
    assert l["desconto"] == "" and l["desconto_pct"] == ""


def test_desconto_de_verdade_continua_riscado_e_com_o_percentual():
    """Negociação o cliente PRECISA ver — desconto que ele não vê não vende."""
    l = _linhas([{"nome": "PACOTE PRIME", "setup": 8800, "qtd": 1,
                  "unitario": 8800, "desc_tipo": "valor", "desc_val": 1000}])[0]
    assert l["incluso"] is False
    assert l["cheio"] and l["desconto"] and l["desconto_pct"]
    assert l["tabela"] == ""


def test_linha_sem_desconto_nenhum_nao_ganha_enfeite():
    l = _linhas([{"nome": "LOCAÇÃO SUÍTE", "setup": 250, "qtd": 1, "unitario": 250}])[0]
    assert l["incluso"] is False
    assert l["cheio"] == "" and l["tabela"] == "" and l["desconto"] == ""


def test_o_numero_23_da_prime_sai_com_nove_inclusos_e_o_mesmo_total():
    """O orçamento real: dez linhas zeradas com 100%, uma com R$ 1.000 de
    abatimento e a suíte cheia. Nove são "incluso"; a do pacote é desconto de
    verdade. E a soma continua R$ 8.050."""
    itens = [
        {"nome": "PACOTE PRIME 2027", "setup": 8800, "qtd": 1, "unitario": 8800,
         "desc_tipo": "valor", "desc_val": 1000},
        {"nome": "SUÍTE", "setup": 250, "qtd": 1, "unitario": 250,
         "desc_tipo": "pct", "desc_val": 0},
    ] + [
        {"nome": n, "setup": v, "qtd": 1, "unitario": v,
         "desc_tipo": "pct", "desc_val": 100}
        for n, v in (("MESA", 4000), ("VASOS", 2500), ("POLTRONAS", 1600),
                     ("COZINHA", 1500), ("CLIMATIZADORES", 1000), ("GERADOR", 1000),
                     ("UTENSÍLIOS", 1000), ("LEDS", 750), ("FREEZER", 500))
    ]
    linhas = _linhas(itens)
    assert sum(1 for l in linhas if l["incluso"]) == 9
    assert [l["incluso"] for l in linhas[:2]] == [False, False]
    assert dsc.somar_itens(itens)["setup"] == 8_050_00


def test_a_marca_explicita_tambem_imprime_incluso():
    """A tela nova grava `incluso: true` junto com os 100% — a folha tem que
    entender os dois jeitos de dizer a mesma coisa."""
    l = _linhas([{"nome": "LEDS", "setup": 750, "qtd": 15, "unitario": 50,
                  "incluso": True, "desc_tipo": "pct", "desc_val": 100}])[0]
    assert l["incluso"] is True
