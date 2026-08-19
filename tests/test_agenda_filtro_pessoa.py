"""Filtrar por pessoa não pode custar uma página inteira.

O QUE ERA. Os nomes acima do calendário eram links. Cada clique refazia a tela do
zero — treze consultas ao banco, ~139 KB de HTML — para o servidor rodar, no fim
de tudo, duas linhas de Python sobre uma lista que ele já tinha na mão:

    eventos = [e for e in eventos if e.get("membro_id") == p_id]

E junto ia o resto: o mês aberto, a rolagem, o dia expandido. Era isso que fazia
a tela piscar a cada toque.

O QUE É AGORA. O servidor manda o mês inteiro e diz apenas qual chip nasce
ligado; quem esconde e mostra é a tela, a partir de `membro_id`, que passou a
viajar em cada linha. Zero requisição.

O SERVIDOR TEVE QUE PARAR DE FILTRAR — não é detalhe de implementação, é a
condição pra isto funcionar: se ele mandasse só os eventos do Rafael, clicar em
"Todos" não teria de onde trazer os outros de volta sem recarregar, que é
exatamente o que se estava tirando.

Testa-se aqui o CONTRATO entre servidor e tela: o dado que a tela precisa chega,
e o servidor não pré-filtra. O comportamento do clique é do navegador; que o
código compile está em tests/test_painel_js_sintaxe.py.
"""
from datetime import date, datetime, timedelta

import pytest

from finance import agenda as ag
from web import painel_agenda as pa


def _ev(id_, membro_id, dia=15, hora=19):
    return {"id": id_, "titulo": f"Festa {id_}", "tipo": "empresa",
            "inicio": datetime(2026, 9, dia, hora, 0, tzinfo=ag.BRT),
            "status": "ativo", "membro_id": membro_id}


# ------------------------------------------- o dado que a tela precisa chega

def test_cada_linha_do_calendario_diz_de_quem_e():
    """Sem `membro_id` na linha, o filtro na tela é impossível e a única saída
    volta a ser recarregar a página."""
    semanas = pa._monta_semanas(2026, 9, [_ev(1, 7), _ev(2, None)], date(2026, 9, 1))
    linhas = [l for sem in semanas for c in sem for l in c["eventos"]]
    assert {l["id"]: l["membro_id"] for l in linhas} == {1: 7, 2: ""}


def test_a_caixa_do_dia_tambem_diz_de_quem_e():
    """A caixa do dia é redesenhada pelo mesmo filtro. Abrir o dia embaixo do nome
    do Rafael e ver o compromisso da Ana seria a tela se contradizendo."""
    por_dia = pa._eventos_por_dia([_ev(1, 7), _ev(2, None)])
    evs = por_dia["2026-09-15"]["eventos"]
    assert {e["id"]: e["membro_id"] for e in evs} == {1: 7, 2: ""}


def test_dono_titular_vira_vazio_e_nao_none():
    """`None` viraria a string "None" no atributo HTML e nunca casaria com chip
    nenhum — o dono titular sumiria de qualquer filtro, inclusive do "Todos"."""
    semanas = pa._monta_semanas(2026, 9, [_ev(1, None)], date(2026, 9, 1))
    linha, = [l for sem in semanas for c in sem for l in c["eventos"]]
    assert linha["membro_id"] == ""


# ------------------------------------------ o servidor não pré-filtra mais

def test_o_servidor_nao_filtra_por_pessoa():
    """A linha que fazia o clique custar uma página inteira. Se ela voltar, o
    "Todos" para de funcionar sem recarregar — e o problema volta inteiro."""
    import inspect
    src = inspect.getsource(pa.agenda_home)
    assert 'e.get("membro_id") == p_id' not in src
    assert "p_id" in src, "o ?p= ainda precisa dizer qual chip nasce ligado"


# --------------------------------------------------- o contrato com o HTML

def test_os_chips_carregam_o_id_da_pessoa():
    tpl = pa._AGENDA_TPL
    assert 'class="agp{% if p_id is none %} on{% endif %}" data-membro=""' in tpl, \
        "o chip Todos precisa do data-membro vazio"
    assert 'data-membro="{{ pp.id }}"' in tpl


def test_as_linhas_de_proximos_carregam_a_pessoa():
    assert 'class="px-row" data-ev="{{ e.id }}" data-membro=' in pa._AGENDA_TPL


def test_os_chips_continuam_links_de_verdade():
    """Continuam <a href> com o ?p= certo: o link é copiável, e quem cair nele
    direto vê o filtro aplicado. O JS intercepta o clique — não substitui a rota."""
    assert '&p={{ pp.id }}" class="agp' in pa._AGENDA_TPL


# ------------------------------------------------- o filtro é UM só, na tela

def test_calendario_e_caixa_do_dia_leem_o_mesmo_filtro():
    """Dois critérios de filtragem seriam dois calendários. `agDoFiltro` é o
    único lugar que decide."""
    js = pa._JS_CRU
    assert "function agDoFiltro(e)" in js
    assert js.count("agDoFiltro") >= 3      # a definição + as duas leituras
    assert ".filter(agDoFiltro)" in js


def test_dia_vazio_pelo_filtro_nao_perde_o_data_iso():
    """A célula que ficou sem eventos DO FILTRO não é uma célula vazia: limpar o
    filtro tem que trazê-la de volta. Perder o `data-iso` a mataria pra sempre."""
    js = pa._JS_CRU
    assert "if(REAPROVEITAR.length || todos.length){ cel.classList.add('clicavel'); }" in js


# ------------------------------------------- a seta do mês, também sem recarregar

def test_a_seta_do_mes_troca_por_fetch_e_nao_por_link():
    """O filtro por pessoa ficou instantâneo e a seta não — era o que faltava. Ela
    continua sendo <a href> de verdade (sem JS, ou se o fetch falhar, o link vale),
    mas ganhou `data-m`, que é o que o JS lê pra trocar sem recarregar."""
    tpl = pa._AGENDA_TPL
    assert 'data-m="{{ mes_prev }}"' in tpl and 'data-m="{{ mes_next }}"' in tpl
    assert 'data-m="{{ mes_hoje }}" class="ag-hoje"' in tpl
    # o href continua completo: é o plano B, não enfeite
    assert 'href="/painel/agenda?m={{ mes_prev }}"' in tpl


def test_a_troca_de_mes_usa_o_mesmo_desenhador_de_celula():
    """Dois lugares desenhando célula é o começo de dois calendários. `irParaMes`
    monta a grade vazia e chama `renderizarCelula` — o mesmo que o cancelar, o
    remarcar e o filtro por pessoa já usavam."""
    js = pa._JS_CRU
    assert "function irParaMes(" in js and "function montarGrade(" in js
    assert "Object.keys(EVENTOS_DIA).forEach(renderizarCelula)" in js


def test_falhar_o_fetch_volta_pro_link_de_sempre():
    """Rede caindo não pode deixar a agenda travada num mês. O catch devolve o
    comportamento antigo em vez de engolir o clique."""
    assert "window.location.href = '/painel/agenda?m='" in pa._JS_CRU


def test_o_botao_voltar_volta_de_mes():
    """A contrapartida de trocar link por fetch: sem tratar `popstate`, o voltar do
    navegador sairia da agenda em vez de voltar um mês."""
    js = pa._JS_CRU
    assert "history.pushState" in js and "popstate" in js

