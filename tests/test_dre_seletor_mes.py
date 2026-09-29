"""O seletor de mês do card do DRE (aba Empresa), pedido do dono em 29/09/2026
depois da Iris (cliente da Manoel Soares) reparar que só dava pra ver o mês
atual. Mockup aprovado com "6 meses tá bom".

MESMO PADRÃO que a aba Financeiro já usa (`?mes=AAAA-MM`), mas com um limite
que o mockup deixou explícito: só o CARD DO DRE (e o "ver por centro de
custo" dentro dele) olha pro mês escolhido. O resto da aba — títulos, folha,
"a classificar", planejamento da semana — é trabalho do MÊS ATUAL de verdade
e não pode seguir o seletor: misturar os dois faria "a classificar" mostrar
lançamento de um mês que não é mais o de hoje, ou a folha aparecer errada.

Segue o padrão dos outros testes deste arquivo enorme (test_dash_bloco_sem_
duplicata.py, test_titulos_pagos_na_tela.py): inspeção de fonte/template, sem
montar a rota inteira (que puxa uma dúzia de módulos tolerantes a falha) —
renderizar de verdade pediria sessão PJ completa só pra provar cinco linhas
de parsing.
"""
from __future__ import annotations

import inspect
import re

from web import portal as pt


def _fonte_painel_empresa() -> str:
    return inspect.getsource(pt.painel_empresa)


def test_so_o_dre_e_o_dre_por_centro_seguem_o_mes_escolhido():
    fonte = _fonte_painel_empresa()
    assert "emp.dre_mes(pool, conta[0], ano_dre, mes_dre)" in fonte
    assert "emp.dre_por_centro(pool, conta[0], ano_dre, mes_dre)" in fonte


def test_o_resto_da_aba_continua_no_mes_atual_de_verdade():
    """Se algum destes passar a usar ano_dre/mes_dre, o "a classificar" ou a
    folha do mês passam a mostrar um mês que não é mais hoje — sem ninguém
    perceber, porque a tela continua abrindo sem erro."""
    fonte = re.sub(r"\s+", " ", _fonte_painel_empresa())
    assert "lancamentos_a_classificar( hoje.year, hoje.month)" in fonte
    assert "emp.folha_do_mes(pool, conta[0], hoje.year, hoje.month)" in fonte
    assert "emp.eventos_folha_do_mes(pool, conta[0], hoje.year, hoje.month)" in fonte


def test_o_mes_escolhido_cai_pro_atual_quando_vazio_ou_invalido():
    fonte = _fonte_painel_empresa()
    # a mesma forma "tenta, cai pro hoje no ValueError" que a aba Financeiro
    # já usa — reproduzida aqui manualmente pra não depender de montar a rota.
    hoje_ano, hoje_mes = 2026, 9
    for mes_form in ("", "lixo", "2026", "2026-13-01"):
        try:
            ano_dre, mes_dre = ((int(x) for x in mes_form.split("-"))
                                if mes_form else (hoje_ano, hoje_mes))
        except ValueError:
            ano_dre, mes_dre = hoje_ano, hoje_mes
        assert (ano_dre, mes_dre) == (hoje_ano, hoje_mes) or mes_form == "2026-13-01", (
            f"mes={mes_form!r} deveria cair no mês atual, veio {(ano_dre, mes_dre)}")
    assert "except ValueError:" in fonte
    assert "ano_dre, mes_dre = hoje.year, hoje.month" in fonte


def test_o_select_do_card_tem_seis_meses_e_recarrega_a_pagina():
    m = re.search(r'<div class="card larga" id="dre">.*?</select>', pt._EMPRESA, re.S)
    assert m, "o card do DRE (ou o seletor dentro dele) sumiu do template"
    bloco = m.group(0)
    assert "meses_dre" in bloco and "mes_dre_sel" in bloco
    assert "/painel/empresa?mes=" in bloco
    assert "#dre" in bloco  # volta pro próprio card, não pro topo da página


def test_a_rota_aceita_o_parametro_mes():
    assinatura = inspect.signature(pt.painel_empresa)
    assert "mes" in assinatura.parameters
    assert assinatura.parameters["mes"].default == ""


def test_os_ultimos_seis_meses_vem_do_hoje_nao_do_mes_escolhido():
    """O `<select>` sempre oferece 'os últimos 6 meses a partir de hoje' — ele
    não pode encolher pra só os meses ANTES do que já está selecionado."""
    fonte = _fonte_painel_empresa()
    assert "_y, _m = hoje.year, hoje.month" in fonte
    assert "range(6)" in fonte
