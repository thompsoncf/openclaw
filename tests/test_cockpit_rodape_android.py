"""O rodapé do app do vendedor não pode nascer atrás da barra do Android.

O `_page` pede `viewport-fit=cover` de propósito: sem ele sobra tarja preta no topo
do app instalado. O preço é que a página passa a ser desenhada POR BAIXO das barras
do sistema, e quem faz isso tem que devolver o espaço embaixo.

Até 23/08 a conta era só `env(safe-area-inset-bottom, 0px)`. No iPhone ele reporta
~34px e tudo funcionava; no Android com os três botões ele volta **zero**, e aí o
rodapé reservava só o próprio padding. Medido num Chromium com o CSS real do app:

    padding-bottom: 12,8px  ->  o botão parava a 13px do fim da tela

A barra de navegação come ~48px. O "Marcar" da tela de agendar visita nascia inteiro
atrás dela — o vendedor via só a borda verde de cima, e não tinha como salvar. Depois
do piso a mesma medição dá 61px de folga.

Este arquivo é de FONTE, não de render: garante que o piso existe e que nenhuma
âncora de baixo voltou a depender só do `env()`. A medição no navegador está em
test_cockpit_rodape_render.py, que pula sozinho onde não há Chromium.
"""
import re

from web import painel_cockpit as pc

#: as regras que encostam no fim da tela — todas passaram a usar o token
_ANCORAS = (".rodape{", ".folha{", ".tabs{", ".rodape-b{")


def _bloco(seletor: str) -> str:
    """O corpo da regra CSS daquele seletor."""
    i = pc._CSS.index(seletor)
    return pc._CSS[i:pc._CSS.index("}", i)]


def test_o_piso_existe_e_deixa_o_env_vencer_quando_ele_funciona():
    """`max()` e não um valor fixo: onde o `env()` reporta de verdade (iPhone, ~34px)
    ele continua mandando. O piso só entra quando o sistema não diz nada."""
    tok = _bloco(":root{--fundo-seguro")
    assert "max(" in tok, "sem max() o piso apagaria o valor real do aparelho"
    assert "env(safe-area-inset-bottom" in tok
    assert re.search(r"max\(env\(safe-area-inset-bottom,\s*0px\),\s*3rem\)", tok), \
        "o piso saiu do lugar ou mudou de tamanho sem passar por aqui"


def test_nenhuma_ancora_de_baixo_depende_so_do_env():
    """A trava que teria pego o bug: bastava UMA regra com o `env()` cru pra a tela
    dela voltar a esconder o botão no Android."""
    for sel in _ANCORAS:
        corpo = _bloco(sel)
        assert "var(--fundo-seguro)" in corpo, f"{sel} não usa o piso"
        assert "env(safe-area-inset-bottom" not in corpo, \
            f"{sel} voltou a depender só do env(), que é zero no Android"


def test_o_topo_continua_no_env_puro():
    """O de cima NÃO leva piso, e isso é de propósito: lá o `env()` é a única medida
    certa (altura da barra de status) e um piso empurraria o título pra baixo à toa
    em todo aparelho sem recorte."""
    wrap = _bloco(".wrap{")
    assert "padding-top:env(safe-area-inset-top,0px)" in wrap
    assert "--fundo-seguro" not in wrap


def test_a_barra_de_abas_tambem_e_alcancavel():
    """A `.tabs` fica colada embaixo igual ao rodapé. Ficou de fora do conserto
    original justamente por não ser um botão de ação — mas navegação escondida atrás
    da barra do sistema é o mesmo problema."""
    assert "var(--fundo-seguro)" in _bloco(".tabs{")
