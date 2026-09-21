"""As duas telas que estouravam a largura no celular, e por quê.

O DEFEITO É UM SÓ, com duas caras. O `body` do painel é

    body{display:flex;flex-direction:column;align-items:center}

e `align-items:center` NÃO estica os filhos: cada bloco de tela nasce do tamanho
do PRÓPRIO CONTEÚDO. Quem tem uma tabela larga dentro nasce largo — e aí não é a
tabela que rola, é a PÁGINA, levando junto o cabeçalho, os filtros e os cartões.
Foi o que o dono viu em 21/09/2026: no Raio-X o nome do vendedor saía pela
esquerda; no Financeiro o saldo do mês vinha cortado na borda.

MEDIDO, não estimado, num Chromium a 390px:

    Raio-X      · a tabela por vendedor pede 611px → `.rx` nascia com 611px
    Financeiro  · `R$ -14.773,56` pede 182px numa coluna de 146px → 453px de página

`overflow-x:auto` NÃO BASTA e é a parte contraintuitiva: ele zera o min-content
do filho, mas o MAX-content continua sendo o conteúdo inteiro — e é o max-content
que um flex item centralizado usa pra se dimensionar.
"""
from web.painel_raio_x import _RAIO_X_TPL


def _css_do_raio_x() -> str:
    return _RAIO_X_TPL[_RAIO_X_TPL.index("<style>"):_RAIO_X_TPL.index("</style>")]


# ----------------------------------------------------------------- Raio-X
def test_o_raio_x_tem_teto_de_largura():
    """Sem o teto, `.rx` se dimensiona pelo max-content — a tabela de 611px."""
    css = _css_do_raio_x()
    regra = [l for l in css.split("\n") if l.startswith(".rx{")]
    assert regra, "a regra .rx sumiu"
    assert "max-width:100%" in regra[0], \
        "sem max-width o .rx nasce do tamanho da tabela e a PÁGINA rola, não a tabela"


def test_a_tabela_do_raio_x_continua_rolando_por_dentro():
    """O teto só funciona junto com o `overflow-x` — um sem o outro não resolve:
    sem o teto a página rola; sem o overflow a tabela fica cortada sem saída."""
    css = _css_do_raio_x()
    assert ".rx-tab{overflow-x:auto}" in css


# ------------------------------------------------------------- Financeiro
def test_o_card_do_saldo_nao_vira_duas_colunas_no_celular():
    """Trilha `1fr` tem mínimo automático = min-content, e o `white-space:nowrap`
    do valor faz o min-content ser o número inteiro. Duas trilhas dessas não
    cabem, e as trilhas se recusam a encolher: o card empurra a página."""
    fonte = open("web/portal.py", encoding="utf-8").read()
    assert ".fin-cards>div:first-child{display:grid !important;grid-template-columns:1fr 1fr" not in fonte, \
        "o card do saldo voltou a virar duas colunas no celular — não cabe"


def test_o_valor_do_saldo_encolhe_no_celular():
    """Empilhado o número cabe a 390px, mas num iPhone SE (320px) a 1.5rem ele
    pedia 182px numa coluna de 180. 1.25rem pede 151px e sobra folga."""
    fonte = open("web/portal.py", encoding="utf-8").read()
    assert ".fin-cards>div:first-child b{font-size:1.25rem}" in fonte


def test_a_borda_lateral_do_saldo_saiu_junto():
    """A borda da esquerda só fazia sentido lado a lado. Empilhado ela ficaria
    pendurada, e a linha de cima (do comportamento base) é que separa os dois."""
    fonte = open("web/portal.py", encoding="utf-8").read()
    assert "border-left:1px solid #1e1e20;padding-left:.6rem !important" not in fonte
