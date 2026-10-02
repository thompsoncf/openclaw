"""O filtro de período dos Relatórios (01/10/2026, pedido do dono: "coloca uma
opção por período").

Até aqui só a Agenda tinha "Período específico": as outras abas chamavam
`_intervalo(periodo)` sem `de`/`ate`, e oferecer a opção nelas devolveria o mês
corrente calado. Agora toda aba com período repassa as datas — e este arquivo
confere, ABA POR ABA, que elas chegam no cálculo do intervalo. Aba nova que
esquecer de repassar falha aqui, em vez de mentir na tela.

Roda sem banco: o `_intervalo` falso interrompe a aba antes da consulta.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

import pytest

from finance import periodo as per
from web import painel_relatorios as rel

#: fotografia do que está em aberto: o período não filtra (a tela diz isso)
SEM_PERIODO = {"contas_pagar", "contas_receber"}


class _Parou(Exception):
    pass


@pytest.mark.parametrize("tipo", [t for t in rel.TIPOS if t not in SEM_PERIODO])
def test_toda_aba_com_periodo_repassa_as_datas(tipo, monkeypatch):
    vistos = []

    def _falso(periodo, de=None, ate=None, ate_o_fim=False):
        vistos.append((periodo, de, ate))
        raise _Parou

    monkeypatch.setattr(rel, "_intervalo", _falso)
    with pytest.raises(_Parou):
        rel.TIPOS[tipo]["montar"](None, 1, "personalizado", status="", vendedor="", q="",
                                  especie="", de="2026-09-01", ate="2026-09-15",
                                  data_por="")
    assert vistos == [("personalizado", "2026-09-01", "2026-09-15")]


@pytest.mark.parametrize("tipo", [t for t in rel.TIPOS if t not in SEM_PERIODO])
def test_toda_aba_com_periodo_oferece_o_periodo_especifico(tipo):
    assert "personalizado" in dict(rel.periodos_da_aba(tipo))


def test_o_periodo_especifico_recorta_as_datas_escolhidas():
    assert per.intervalo("personalizado", "2026-09-01", "2026-09-15") == (
        date(2026, 9, 1), date(2026, 9, 15))
    # invertidas = engano de digitação
    assert per.intervalo("personalizado", "2026-09-15", "2026-09-01") == (
        date(2026, 9, 1), date(2026, 9, 15))


def test_os_ultimos_90_dias_sao_90_dias_contando_hoje():
    """Mesma régua das janelas de 7/14/30 dias. Antes eram 91."""
    for p, n in (("7d", 7), ("14d", 14), ("30d", 30), ("90d", 90)):
        ini, fim = per.intervalo(p)
        assert (fim - ini) + timedelta(days=1) == timedelta(days=n), p


def test_este_mes_vai_do_dia_1_ate_hoje_no_dia_de_brasilia():
    ini, fim = per.intervalo("mes")
    assert ini.day == 1 and ini.month == fim.month and ini.year == fim.year
    assert fim >= ini


# ------------------------------------------------------- a tela (Jinja de verdade)
def _tela(tipo, *, filtro_extra=None):
    from web.portal import _env
    dados = {"label": rel.TIPOS[tipo]["label"], "mock": False,
             "colunas": [rel._col("descricao", "Descrição")], "linhas": []}
    if filtro_extra:
        dados["filtro_extra"] = filtro_extra
    return _env.get_template("relatorios").render(
        dados=dados, tipo=tipo, periodo="personalizado",
        periodo_rotulo="01/09/2026 a 15/09/2026", periodos=rel.periodos_da_aba(tipo),
        tem_periodo_livre=True, de="2026-09-01", ate="2026-09-15", tipos=rel.TIPOS,
        conta=(1, "pj", "X"), caps={"financeiro": True, "vendas": True, "gerir": True},
        tem_pj=True, papel="dono", request=None)


def test_vendas_mostra_as_datas_e_um_botao_pra_filtrar():
    """Vendas não tem a barra de filtros extras — e portanto nem o Filtrar dela.
    Sem um botão junto das datas, escolher o período não teria como ir."""
    html = _tela("vendas")
    caixa = re.search(r'<span class="rel-datas" id="rel-datas"(.*?)</span>\s*\n', html, re.S)
    assert caixa and 'name="de" value="2026-09-01"' in caixa.group(1)
    assert 'name="ate" value="2026-09-15"' in caixa.group(1)
    assert 'class="rel-filtrar"' in caixa.group(1)
    assert 'value="personalizado" selected' in html


def test_trocar_de_aba_leva_as_datas_junto():
    html = _tela("vendas")
    assert "tipo=comissao&periodo=personalizado&de=2026-09-01&ate=2026-09-15" in html


# ------------------------------------------------ o corte é da TELA, não da conta
def test_cortar_pra_tela_mostra_300_e_diz_quantas_existem():
    poucas = rel._cortar_pra_tela({"linhas": [{"x": i} for i in range(10)]})
    assert len(poucas["linhas"]) == 10 and poucas["linhas_total"] == 10
    muitas = rel._cortar_pra_tela({"linhas": [{"x": i} for i in range(350)]})
    assert len(muitas["linhas"]) == 300 and muitas["linhas_total"] == 350
    assert muitas["linhas"][0] == {"x": 0}, "as primeiras (as mais recentes) ficam"


def test_nenhuma_consulta_corta_antes_da_conta():
    """O teto de 300/500 na consulta foi o que truncava o total. Nenhuma aba volta
    a ter: quem corta é o `_cortar_pra_tela`, depois da conta feita."""
    import inspect
    fonte = inspect.getsource(rel)
    assert "limit 300" not in fonte and "limit 500" not in fonte
    assert "limite=300" not in fonte


def test_a_tela_avisa_quando_corta():
    from web.portal import _env
    dados = {"label": "Vendas", "mock": False, "colunas": [rel._col("descricao", "Descrição")],
             "linhas": [{"descricao": "x"}] * 300, "linhas_total": 350}
    html = _env.get_template("relatorios").render(
        dados=dados, tipo="vendas", periodo="todos", periodo_rotulo="Todo o período",
        periodos=rel.periodos_da_aba("vendas"), tem_periodo_livre=True, de="", ate="",
        tipos=rel.TIPOS, conta=(1, "pj", "X"),
        caps={"financeiro": True, "vendas": True, "gerir": True},
        tem_pj=True, papel="dono", request=None)
    assert "Mostrando 300 de 350 registros" in html


def test_o_funil_olha_o_mes_inteiro_como_a_agenda(monkeypatch):
    """Decisão do dono (01/10/2026): a visita marcada pro dia 20 conta como agendada
    desde que foi marcada, não só quando o dia chega."""
    vistos = []

    def _falso(periodo, de=None, ate=None, ate_o_fim=False):
        vistos.append(ate_o_fim)
        raise _Parou

    monkeypatch.setattr(rel, "_intervalo", _falso)
    with pytest.raises(_Parou):
        rel._dados_funil(None, 1, "mes", "", "", "")
    assert vistos == [True]


# ------------------------------------- 01/10/2026: a tela tem que DIZER o intervalo
# O dono: "quando coloco as datas o sistema não informa os intervalos". A tela
# dizia só "período: Este mês" — e o mesmo "Este mês" é 01/10 a hoje em Vendas e
# o mês inteiro na Agenda e no Funil. Agora as datas aparecem no rótulo, em cada
# opção do seletor e nas caixas do "Período específico…", e saem do MESMO
# `_intervalo` da consulta: o escrito é o filtrado.
@pytest.mark.parametrize("tipo", [t for t in rel.TIPOS if t not in SEM_PERIODO])
@pytest.mark.parametrize("periodo", ["mes", "mes_passado", "90d", "ano"])
def test_o_rotulo_diz_as_datas_que_a_consulta_usa(tipo, periodo):
    ini, fim = per.intervalo(periodo, ate_o_fim=(tipo in ("agenda", "funil")))
    rot = rel._rotulo_periodo(tipo, periodo, "", "")
    assert rot.endswith(f"{ini:%d/%m/%Y} a {fim:%d/%m/%Y}"), rot
    assert rot.startswith(per.ROTULO[periodo])


def test_este_mes_e_ate_hoje_no_historico_e_o_mes_todo_na_agenda():
    hoje = per.intervalo("mes")[1]
    assert rel._rotulo_periodo("vendas", "mes", "", "").endswith(f"a {hoje:%d/%m/%Y}")
    fim_mes = per.fim_do_mes(hoje)
    assert rel._rotulo_periodo("agenda", "mes", "", "").endswith(f"a {fim_mes:%d/%m/%Y}")


def test_todo_o_periodo_diz_ate_quando():
    assert rel._rotulo_periodo("vendas", "todos", "", "").startswith("Todo o período · até ")


def test_cada_opcao_do_seletor_mostra_o_intervalo():
    ops = {o["v"]: o for o in rel._periodos_com_datas("vendas")}
    ini, fim = per.intervalo("mes")
    assert ops["mes"]["rot"] == f"Este mês ({ini:%d/%m} a {fim:%d/%m})"
    assert (ops["mes"]["de"], ops["mes"]["ate"]) == (ini.isoformat(), fim.isoformat())
    assert ops["personalizado"]["rot"] == "Período específico…"
    assert ops["todos"]["de"] == "", "o 01/01/2000 técnico não vai pra caixa"


def test_a_tela_mostra_as_datas_no_seletor_e_preenche_as_caixas():
    from web.portal import _env
    dados = {"label": "Vendas", "mock": False, "colunas": [rel._col("descricao", "D")],
             "linhas": []}
    html = _env.get_template("relatorios").render(
        dados=dados, tipo="vendas", periodo="mes",
        periodo_rotulo=rel._rotulo_periodo("vendas", "mes", "", ""),
        periodos=rel.periodos_da_aba("vendas"), periodos_datas=rel._periodos_com_datas("vendas"),
        tem_periodo_livre=True, de="", ate="", tipos=rel.TIPOS, conta=(1, "pj", "X"),
        caps={"financeiro": True, "vendas": True, "gerir": True},
        tem_pj=True, papel="dono", request=None)
    ini, fim = per.intervalo("mes")
    assert f'data-de="{ini.isoformat()}" data-ate="{fim.isoformat()}"' in html
    assert f"Este mês ({ini:%d/%m} a {fim:%d/%m})" in html
    assert f"período: Este mês · {ini:%d/%m/%Y} a {fim:%d/%m/%Y}" in html
    assert "getAttribute('data-ant')" in html, "o Período específico pega as datas do anterior"


def test_contas_em_aberto_nao_tem_seletor_de_periodo_que_nao_filtra():
    """Contas a pagar/receber mostram tudo que está em aberto: um seletor ali é
    filtro que não faz nada. Some — e o período segue escondido pras outras abas."""
    from web.portal import _env
    dados = {"label": "Contas a pagar", "mock": False, "sem_periodo": True,
             "colunas": [rel._col("descricao", "D")], "linhas": []}
    html = _env.get_template("relatorios").render(
        dados=dados, tipo="contas_pagar", periodo="mes_passado", periodo_rotulo="x",
        periodos=rel.periodos_da_aba("contas_pagar"),
        periodos_datas=rel._periodos_com_datas("contas_pagar"),
        tem_periodo_livre=True, de="", ate="", tipos=rel.TIPOS, conta=(1, "pj", "X"),
        caps={"financeiro": True, "vendas": True, "gerir": True},
        tem_pj=True, papel="dono", request=None)
    assert '<select name="periodo"' not in html
    assert '<input type="hidden" name="periodo" value="mes_passado">' in html
    assert 'name="de"' not in html
