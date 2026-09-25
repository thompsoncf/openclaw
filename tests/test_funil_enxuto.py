"""O funil enxuto — pedido do dono em 24/09/2026 ("dê uma olhada no layout desse
funil e veja algo mais profissional"). Mockup aprovado em
docs/mockups/prospeccao_layout.html, com as decisões do dono: o funil usa a tela
inteira, excluir lead fica só pra dono e gestor, e o período padrão continua o mês
atual.

Esta é a PARTE 1 (o cartão e as colunas). O que este teste protege:

  * **excluir é de dono e gestor** — no servidor, não só na tela: excluir é DELETE,
    sem volta, e até aqui qualquer papel apagava a um clique do nome;
  * **o card em 4 linhas não esconde nada**: o ⋯ e o avatar usam data-lead (é por
    data-id que os testes e o arrastar acham o card), e a lista de responsáveis é
    UMA na página (eram ~1.100 <option> na Prime);
  * **coluna vazia vira trilho** e **o cabeçalho diz esperando e parados**;
  * **as contagens não mentem depois de mover**: o "de T" da coluna fica, e o
    total da conta só anda ±1;
  * **o reload de 60 s não fecha nada debaixo de quem está lendo**.
"""
import inspect

from tests.test_painel_js_sintaxe import _render
from web import painel_prospeccao as pp

TPL = pp._KANBAN_TPL
FONTE = inspect.getsource(pp)


def test_excluir_so_dono_e_gestor_no_servidor():
    rota = inspect.getsource(pp.prospeccao_excluir)
    assert 'if not ctx["gerencia"]:' in rota
    assert rota.index('if not ctx["gerencia"]:') < rota.index("delete from prospeccao"), (
        "a trava tem que vir antes do DELETE")


def test_na_tela_o_excluir_some_pra_quem_nao_e_gerencia():
    assert "window.KB_EXCLUI={{ 'true' if gerencia else 'false' }}" in TPL
    menu = FONTE.split("function kbMenu(")[1][:2200]
    assert "if(window.KB_EXCLUI)" in menu and "kbExcluir(null," in menu
    assert '{% if gerencia %}<button type="button" class="pbtn ghost" onclick="baseExcluir(' in FONTE, (
        "na Base o 🗑 também é só de dono e gestor")


def test_o_card_tem_quatro_linhas_e_botoes_por_data_lead():
    html = _render("prospeccao", pode_atribuir=True, gerencia=True,
                   vendedores=[{"id": 9, "nome": "Jacqueline Prime", "papel": "vendedor"}])
    for peca in ('class="kbl1"', 'class="kbl2"', 'class="kbl4"', 'class="kbmais"'):
        assert peca in html, peca
    assert 'class="kbx"' not in html, "o ✕ de excluir voltou pra linha do nome"
    assert 'class="kbvend"' not in html, "voltou o <select> de vendedor em cada card"
    assert html.count('id="kbvpop"') == 1 and html.count('id="kbmenu"') == 1
    macro = TPL[TPL.index("{% macro kbcard"):TPL.index("{%- endmacro %}")]
    assert 'data-lead="{{ c.id }}"' in macro
    assert macro.count('data-id="{{ c.id }}"') == 1, "botão novo não pode usar data-id"


def test_a_ultima_mensagem_abre_a_conversa_dela():
    macro = TPL[TPL.index("{% macro kbcard"):TPL.index("{%- endmacro %}")]
    assert "kbAbrirChat(event,{{ c.conv_ult }},'{{ c.canal_ult }}',this)" in macro
    assert '"conv": cv_id, "canal": cv_canal' in FONTE


def test_coluna_vazia_vira_trilho_e_o_cabecalho_diz_esperando_e_parados():
    assert '{% if not _ncol %} data-vazia="1"{% endif %}' in TPL
    assert '.kbcol[data-vazia="1"]:not(.aberta){width:44px' in TPL
    assert '<div class="kbcolsub">' in TPL and "esperando</span>" in TPL
    assert "onclick=\"kbParados(this)\"" in TPL


def test_contagem_preserva_o_de_T_e_o_total_so_anda_um():
    recontar = FONTE.split("function kbRecontar()")[1][:900]
    assert "f.nodeValue=" in recontar, "voltou a trocar o chip inteiro e apagar o 'de T'"
    upd = FONTE.split("function updCounts(delta)")[1][:400]
    assert "t+delta" in upd
    assert "updCounts(-1)" in FONTE and "updCounts(1)" in FONTE


def test_card_que_muda_de_coluna_entra_no_topo():
    assert "drop.insertBefore(card,drop.firstChild);kbBrilho(card);" in FONTE
    assert "drop.appendChild(card)" not in FONTE.split("function kbDrop")[1][:600]


def test_o_reload_nao_fecha_a_janela_nem_o_menu_e_volta_onde_estava():
    ciclo = FONTE.split("setInterval(function(){\n  if(document.hidden) return;")[1][:900]
    for freio in (".leadpop", "perdapop", ".etcfg[open]", ".kbpop:not([hidden])"):
        assert freio in ciclo, freio
    assert ciclo.index("kbGuardaTela();") < ciclo.index("location.reload();")
    assert "sessionStorage.getItem('kb_tela')" in FONTE


def test_o_funil_usa_a_tela_inteira_so_ele():
    assert '<div class="pw funil">' in TPL
    assert ".pw.funil{max-width:none" in TPL
    assert ".pw{width:100%;max-width:1240px" in pp._CSS, "as outras telas seguem nos 1240 px"
