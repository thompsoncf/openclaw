"""A regra do desconto, sozinha.

As contas aqui são as MESMAS do mockup aprovado — se elas mudarem, a tela passa a
mostrar um número e o cliente a ler outro, que é exatamente o estrago que este
módulo existe pra impedir.

Tudo puro: nada toca banco.
"""
import pytest

from finance import desconto as dsc


def _item(setup, mensal=0, tipo="pct", desc=0):
    return {"setup": setup, "mensal": mensal, "desc_tipo": tipo, "desc_val": desc}


# ------------------------------------------------- descontos ENCADEIAM

def test_a_conta_do_mockup_inteira():
    """Item a item, soma, e o final sobre o subtotal JÁ descontado."""
    itens = [_item(12400, desc=5),                    # −5%   → 11.780
             _item(1860, tipo="valor", desc=360),     # −360  →  1.500
             _item(2800)]                             # —     →  2.800
    t = dsc.totais(itens, tipo="pct", pct=10)
    assert t["bruto"] == 1706000
    assert t["desconto_itens"] == 98000               # 620 + 360
    assert t["subtotal"] == 1608000
    assert t["desconto_final"] == 160800              # 10% de 16.080
    assert t["total"] == 1447200                      # R$ 14.472


def test_o_final_nao_incide_sobre_o_bruto():
    """A decisão explícita. Se incidisse sobre o bruto, o total seria 14.374 —
    R$ 98 de diferença, invisível na tela e por isso perigosa."""
    itens = [_item(12400, desc=5), _item(1860, tipo="valor", desc=360), _item(2800)]
    t = dsc.totais(itens, tipo="pct", pct=10)
    bruto = 1706000
    sobre_o_bruto = bruto - 98000 - int(bruto * 0.10)
    assert sobre_o_bruto == 1437400
    assert t["total"] == 1447200 and t["total"] != sobre_o_bruto


def test_desconto_final_em_reais():
    t = dsc.totais([_item(1000)], tipo="valor", valor=25000)
    assert t["subtotal"] == 100000 and t["desconto_final"] == 25000
    assert t["total"] == 75000


# ------------------------------------------------- no item é sempre percentual

def test_no_recorrente_o_percentual_cai_igual_nas_duas_pontas():
    """Setup e mensalidade descontados na mesma proporção — é o que deixa o
    financeiro continuar sabendo separá-las pra gerar os dois títulos."""
    liq = dsc.liquido_do_item(_item(3000, mensal=800, desc=15))
    assert liq["setup"] == 255000 and liq["mensal"] == 68000


def test_reais_no_item_vira_o_percentual_equivalente_do_primeiro_ano():
    """A conta do mockup: o item contribui 2.000 + 400×12 = 6.800. R$ 850 sobre
    6.800 são 12,5%, que viram 1.750 de setup e 350 de mensal."""
    it = _item(2000, mensal=400, tipo="valor", desc=850)
    assert dsc.contribuicao(it) == 680000
    assert dsc.percentual_do_item(it) == pytest.approx(12.5)
    liq = dsc.liquido_do_item(it)
    assert liq["setup"] == 175000 and liq["mensal"] == 35000


def test_no_evento_sem_mensalidade_a_conta_e_direta():
    it = _item(1860, tipo="valor", desc=360)
    assert dsc.contribuicao(it) == 186000
    assert dsc.liquido_do_item(it)["setup"] == 150000


# ------------------------------------------------- os limites

def test_desconto_nunca_passa_do_valor():
    """Desconto maior que a base viraria ACRÉSCIMO, e item negativo contamina
    total, margem, parcelas e título. É o teto que dispensa configuração."""
    assert dsc.quanto_desconta(10000, "valor", 0, 999999) == 10000
    assert dsc.quanto_desconta(10000, "pct", 300, 0) == 10000
    assert dsc.liquido_do_item(_item(500, tipo="valor", desc=9999))["setup"] == 0


def test_desconto_nunca_e_negativo():
    assert dsc.quanto_desconta(10000, "valor", 0, -500) == 0
    assert dsc.quanto_desconta(10000, "pct", -30, 0) == 0
    assert dsc.percentual_do_item(_item(1000, desc=-5)) == 0.0


def test_base_zero_nao_divide_por_zero():
    assert dsc.percentual_do_item(_item(0, tipo="valor", desc=100)) == 0.0
    assert dsc.quanto_desconta(0, "pct", 50, 0) == 0
    assert dsc.totais([], tipo="pct", pct=10)["total"] == 0


@pytest.mark.parametrize("lixo", [None, "", "abc", {}, []])
def test_campo_que_a_tela_mandou_torto_vale_zero(lixo):
    """A tela é a fonte, e ela erra. Campo vazio ou texto solto não pode explodir
    o salvamento de um orçamento inteiro."""
    it = {"setup": 1000, "mensal": 0, "desc_tipo": lixo, "desc_val": lixo}
    assert dsc.liquido_do_item(it)["setup"] == 100000


def test_tipo_desconhecido_cai_em_percentual():
    assert dsc._tipo("qualquer") == "pct" and dsc._tipo("VALOR") == "valor"


# ------------------------------------------------- os extras do recorrente

def test_extras_do_recorrente_pegam_o_desconto_final_mas_nao_o_de_item():
    """Infraestrutura, integrações e canais somam ao orçamento sem serem linha —
    então recebem o desconto do total e não têm desconto próprio."""
    t = dsc.totais([_item(1000, desc=50)], tipo="pct", pct=10,
                   extra_setup=50000, extra_mensal=0)
    # item: 1.000 −50% = 500 → 50.000 centavos; + extra 50.000 = 100.000
    assert t["subtotal"] == 100000
    assert t["desconto_itens"] == 50000
    assert t["desconto_final"] == 10000 and t["total"] == 90000


def test_as_pontas_voltam_separadas_e_ja_liquidas():
    """É delas que saem os títulos: um de setup, um recorrente."""
    t = dsc.totais([_item(2000, mensal=400)], tipo="pct", pct=10)
    assert t["setup"] == 180000 and t["mensal"] == 36000
    assert t["setup"] + t["mensal"] * 12 == t["total"]


def test_sem_desconto_nenhum_o_total_e_o_bruto():
    """A regressão que importa: conta que nunca usou desconto não pode ver número
    diferente depois desta mudança."""
    itens = [_item(3000, mensal=800), _item(2000, mensal=400)]
    t = dsc.totais(itens)
    assert t["desconto_total"] == 0
    assert t["total"] == t["bruto"] == t["subtotal"]
    assert t["setup"] == 500000 and t["mensal"] == 120000
