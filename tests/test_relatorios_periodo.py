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
