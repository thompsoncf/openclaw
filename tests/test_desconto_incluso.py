"""O que está na proposta sem ser cobrado — "incluso no pacote".

POR QUE ESTE MÓDULO GANHOU UMA FUNÇÃO. Medindo a Prime (conta 34) em 18/09/2026:
de 217 linhas de orçamento, **122 têm desconto de 100%** e exatamente UMA tem
desconto percentual de verdade. O vendedor usava o campo de desconto como
interruptor de "isto vem junto no pacote", porque a tela não tinha outro.

O preço era a tela mentir: no orçamento nº 23 (Maria Carolina, casamento,
R$ 8.050) dez das onze linhas estão zeradas assim, o resumo anunciava "Economia
de R$ 14.850" — e não houve desconto nenhum — e o funil guardava R$ 22.900 de
bruto para um negócio de R$ 8.050.

O QUE ESTE TESTE GARANTE, acima de tudo: que reconhecer o "incluso" **não move
um centavo**. Cem por cento de desconto já zerava a linha e continua zerando; a
função só lê a intenção. Se algum dia ela começar a mexer no total, é aqui que
tem que doer.
"""
from __future__ import annotations

from finance import desconto as dsc


# ---------------------------------------------------------- o que conta como incluso

def test_a_marca_nova_diz_incluso():
    assert dsc.eh_incluso({"setup": 1000, "incluso": True}) is True


def test_os_100_por_cento_das_122_linhas_da_prime_dizem_incluso():
    """A leitura de compatibilidade: é assim que as linhas já gravadas falam."""
    assert dsc.eh_incluso({"setup": 1000, "desc_tipo": "pct", "desc_val": 100}) is True


def test_desconto_parcial_nao_e_incluso():
    """Cinquenta por cento é negociação, não cortesia — e some da conta se virar
    "incluso"."""
    assert dsc.eh_incluso({"setup": 1000, "desc_tipo": "pct", "desc_val": 50}) is False


def test_desconto_em_reais_que_zera_a_linha_nao_e_incluso():
    """R$ 1.000 de abatimento num item de R$ 1.000 dá zero na conta, mas foi
    NEGOCIAÇÃO: quem digitou reais estava descontando. Chamar de cortesia mudaria
    o que a folha diz ao cliente sobre o próprio negócio dele."""
    assert dsc.eh_incluso({"setup": 1000, "desc_tipo": "valor", "desc_val": 1000}) is False


def test_linha_sem_desconto_nao_e_incluso():
    assert dsc.eh_incluso({"setup": 1000}) is False


def test_lixo_no_lugar_do_item_nao_derruba():
    for ruim in (None, "texto", 7, []):
        assert dsc.eh_incluso(ruim) is False


# ------------------------------------------------- e o dinheiro continua o mesmo

# O orçamento nº 23 da Prime, como está gravado em produção: dez linhas zeradas
# com 100%, o pacote com R$ 1.000 de abatimento em reais, e a suíte com o valor
# cheio. Bruto R$ 22.900, líquido R$ 8.050.
_NUMERO_23 = [
    {"nome": "PACOTE PRIME 2027", "setup": 8800, "desc_tipo": "valor", "desc_val": 1000},
    {"nome": "SUÍTE", "setup": 250, "desc_tipo": "pct", "desc_val": 0},
    {"nome": "MESA DE MADEIRA", "setup": 4000, "desc_tipo": "pct", "desc_val": 100},
    {"nome": "VASOS PRETOS", "setup": 2500, "desc_tipo": "pct", "desc_val": 100},
    {"nome": "POLTRONAS", "setup": 1600, "desc_tipo": "pct", "desc_val": 100},
    {"nome": "COZINHA", "setup": 1500, "desc_tipo": "pct", "desc_val": 100},
    {"nome": "CLIMATIZADORES", "setup": 1000, "desc_tipo": "pct", "desc_val": 100},
    {"nome": "GERADOR", "setup": 1000, "desc_tipo": "pct", "desc_val": 100},
    {"nome": "UTENSÍLIOS/LOUÇAS", "setup": 1000, "desc_tipo": "pct", "desc_val": 100},
    {"nome": "LEDS", "setup": 750, "desc_tipo": "pct", "desc_val": 100},
    {"nome": "FREEZER", "setup": 500, "desc_tipo": "pct", "desc_val": 100},
]


def test_o_liquido_do_numero_23_continua_8050():
    """O número que o cliente aprovou e que virou título a receber."""
    s = dsc.somar_itens(_NUMERO_23)
    assert s["setup"] == 8_050_00
    assert s["bruto_setup"] == 22_900_00


def test_marcar_incluso_explicitamente_da_exatamente_o_mesmo_total():
    """A tela nova grava `incluso: true` JUNTO com os 100% — este teste é o que
    garante que gravar os dois não muda o dinheiro em nenhum leitor."""
    com_marca = [dict(i, incluso=True) if i.get("desc_val") == 100 and
                 i.get("desc_tipo") == "pct" else i for i in _NUMERO_23]
    assert dsc.somar_itens(com_marca)["setup"] == dsc.somar_itens(_NUMERO_23)["setup"]
    assert dsc.totais(com_marca) == dsc.totais(_NUMERO_23)


def test_o_resumo_separa_o_incluso_do_desconto_de_verdade():
    """Os números que o resumo passa a mostrar no lugar de "Economia de R$ 14.850".

    São nove linhas inclusas somando R$ 13.850 de tabela, e R$ 1.000 de desconto
    de verdade no pacote. Somados dão os 14.850 de antes — a diferença é que
    agora dá pra dizer qual é qual.
    """
    s = dsc.somar_itens(_NUMERO_23)
    assert s["inclusos"] == 9
    assert s["incluso"] == 13_850_00
    assert s["desconto_real"] == 1_000_00
    assert s["desconto"] == 14_850_00      # o de antes, intacto


def test_sem_nenhum_incluso_o_desconto_real_e_o_desconto_inteiro():
    itens = [{"setup": 1000, "desc_tipo": "pct", "desc_val": 10}]
    s = dsc.somar_itens(itens)
    assert s["inclusos"] == 0 and s["incluso"] == 0
    assert s["desconto_real"] == s["desconto"] == 100_00


def test_no_recorrente_o_incluso_conta_a_mensalidade_do_primeiro_ano():
    """Modo recorrente: a linha tem setup E mensalidade, e o valor de tabela do
    que veio junto tem que somar os doze meses, igual ao resto do módulo."""
    s = dsc.somar_itens([{"setup": 100, "mensal": 50, "desc_tipo": "pct", "desc_val": 100}])
    assert s["incluso_setup"] == 100_00
    assert s["incluso_mensal"] == 50_00
    assert s["incluso"] == 100_00 + 50_00 * dsc.MESES_ANO1
    assert s["setup"] == 0 and s["mensal"] == 0
