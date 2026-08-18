"""A barra de abas da Prospecção: uma fonte só, e nunca em duas linhas.

Dois problemas, um conserto.

**O bug.** A barra existia DUAS vezes: a função `_navbar()` e uma cópia escrita à mão
dentro do `_KANBAN_TPL` (a tela do Funil). As duas divergiram — quando a aba
"🎯 Quem atacar" nasceu, entrou na função e ninguém lembrou da cópia. Resultado: quem
estava no Funil não tinha como chegar nela. É isso que o
`test_todas_as_telas_tem_as_mesmas_abas` trava: qualquer aba nova aparece em todas as
telas ou o teste reclama.

**O layout.** 8 abas somam 996px (medido no Chromium com o CSS real), num container útil
de 1208px — então a última descia pra segunda linha assim que o viewport caía abaixo de
~1030px: zoom de 125% numa tela de 1280, janela não maximizada, tablet. Agora são 6 abas
+ ⚙️ (786px) e a barra ROLA em vez de quebrar.

Sem banco: é tudo texto de template e função pura.
"""
import re

import web.painel_conteudo  # noqa: F401 — registra o template da IA Insta no _env
from web import painel_prospeccao as pp

# as telas do módulo que desenham a barra, pelo nome no loader do Jinja
_TELAS = ("prospeccao", "prospeccao_base", "prospeccao_comunicacao",
          "prospeccao_campanhas", "prospeccao_radar")


def _fonte(nome_template: str) -> str:
    from web.portal import _env
    return _env.loader.mapping[nome_template]


def _abas(html: str) -> list[str]:
    """Os rótulos das abas, na ordem, sem as condicionais do Jinja."""
    limpo = re.sub(r"\{%.*?%\}", "", html, flags=re.S)
    return [t.strip() for t in re.findall(r'class="pnav[^"]*"[^>]*>([^<]*)</a>', limpo)
            if t.strip()]


# ── o bug: uma fonte só ────────────────────────────────────────────────────
def test_o_funil_usa_a_navbar_e_nao_uma_copia():
    """A cópia à mão era o que deixava o Funil pra trás. O `<button>` da captação
    dentro da barra era a assinatura dela."""
    tpl = _fonte("prospeccao")
    assert '<button type="button" class="pnav"' not in tpl, \
        "voltou a cópia à mão da barra dentro do template do Funil"
    assert tpl.count('class="pnav-rol"') == 1, "o Funil tem que ter UMA barra, a do _navbar"


def test_todas_as_telas_tem_as_mesmas_abas():
    """A trava que teria pego a divergência de hoje: 'Quem atacar' existia na função e
    não no Funil. Se alguém acrescentar uma aba em um lugar só, cai aqui."""
    esperado = _abas(pp._navbar("base"))
    assert "🎯 Quem atacar" in esperado, "a aba do radar tem que estar na fonte única"
    for tela in _TELAS:
        assert _abas(_fonte(tela)) == esperado, f"a barra de {tela} divergiu"


def test_o_funil_agora_alcanca_quem_atacar():
    """O sintoma concreto do bug, dito em uma linha."""
    assert "🎯 Quem atacar" in _abas(_fonte("prospeccao"))


# ── o layout: nunca quebrar ────────────────────────────────────────────────
def test_a_barra_rola_em_vez_de_quebrar():
    assert "flex-wrap:wrap" not in pp._CSS.split(".pnavbar{")[1].split("}")[0], \
        "a barra voltou a quebrar linha"
    rol = pp._CSS.split(".pnav-rol{")[1].split("}")[0]
    assert "nowrap" in rol and "overflow-x:auto" in rol


def test_a_engrenagem_fica_fora_da_area_que_rola():
    """Numa caixa só, o ⚙️ sairia de vista junto com as abas — e configuração precisa
    estar sempre alcançável. Ele fecha a `.pnav-rol` antes de si."""
    html = pp._navbar("funil")
    fim_rolagem = html.index("</div>")
    # ' cfg"' e não '"pnav cfg"': entre um e outro mora a condicional do Jinja que
    # marca a aba ativa, então a classe nunca é uma string literal contínua.
    assert html.index(' cfg"') > fim_rolagem, "o ⚙️ ficou dentro da caixa que rola"
    assert html.index('class="pnav') < fim_rolagem, \
        "as abas têm que estar DENTRO da caixa que rola"


def test_a_engrenagem_perdeu_o_rotulo_mas_nao_a_acessibilidade():
    """Só o ícone economiza ~100px; sem `title`/`aria-label` viraria um botão mudo."""
    html = pp._navbar("funil")
    assert 'aria-label="Canais"' in html and 'title="Canais"' in html
    assert "⚙️ Canais" not in html, "o rótulo do ⚙️ tinha que sair da barra"


# ── o Captar Lead ──────────────────────────────────────────────────────────
def test_captar_lead_saiu_da_barra_e_virou_botao_no_funil():
    assert "Captar Lead" not in pp._navbar("funil"), "Captar Lead não é aba"
    tpl = _fonte("prospeccao")
    assert 'class="cap-btn"' in tpl and "capToggle()" in tpl, \
        "o botão de captação sumiu do cabeçalho do Funil"


def test_o_atalho_antigo_continua_abrindo_o_painel():
    """Quem tem `?captar=1` salvo, ou vem de outra tela, não pode cair numa página que
    não faz nada."""
    tpl = _fonte("prospeccao")
    assert "captar=1" in tpl and "capToggle" in tpl


# ── o tamanho ──────────────────────────────────────────────────────────────
def test_sao_seis_abas_mais_a_engrenagem():
    """6 abas + ⚙️ = 786px medidos, contra 996px das 8 de antes. Se este número subir,
    a barra vai rolar (não quebra), mas vale saber que subiu."""
    abas = _abas(pp._navbar("funil"))
    assert len(abas) == 7, f"esperado 6 abas + ⚙️, veio {len(abas)}: {abas}"
    assert abas[-1] == "⚙️"
