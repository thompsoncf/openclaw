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
import web.painel_follow_up  # noqa: F401 — idem, a aba do Follow-up
import web.painel_origens  # noqa: F401 — idem, a aba de Origens
from web import painel_prospeccao as pp

# as telas do módulo que desenham a barra, pelo nome no loader do Jinja
_TELAS = ("prospeccao", "prospeccao_base", "prospeccao_comunicacao",
          "prospeccao_campanhas", "prospeccao_radar",
          # entraram em 07/09/2026, quando viraram abas daqui. Estão nesta lista
          # justamente pra não repetir a história da cópia: aba nova tem que
          # aparecer nas oito telas ou o teste abaixo reclama.
          "follow_up", "origens")


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
def test_sao_nove_abas_mais_a_engrenagem():
    """Eram 6 + ⚙️ (786px). A Régua entrou e foram 7; em 07/09/2026 o Follow-up e o
    Origens saíram do menu lateral e são 9 + ⚙️.

    O aviso do teste anterior valia como aviso, não como proibição: a barra ROLA em
    vez de quebrar (test_a_barra_rola_em_vez_de_quebrar), então uma aba a mais custa
    rolagem, não uma segunda linha. A Régua ganhou lugar porque é onde se liga e
    desliga o automático do funil — quem procura isso procura na barra do módulo, e
    esconder configuração de comportamento dentro de outra tela é como se perde uma
    função inteira. Se este número subir de novo, pense duas vezes: a próxima
    provavelmente é submenu, não aba."""
    abas = _abas(pp._navbar("funil"))
    assert len(abas) == 10, f"esperado 9 abas + ⚙️, veio {len(abas)}: {abas}"
    assert abas[-1] == "⚙️"
    assert "⏱️ Régua" in abas
    # A ORDEM É DE USO DIÁRIO (pedido do dono, 07/09/2026): o que se abre todo dia
    # primeiro, o que se configura uma vez por último. Nove abas só cabem porque a
    # barra rola; a ordem é o que evita ter que rolar.
    assert abas[0] == "🔥 Funil" and abas[1] == "📅 Follow-up"
    assert abas.index("⏱️ Régua") == len(abas) - 2


# ── o quadro do funil ──────────────────────────────────────────────────────
def test_o_funil_nao_quebra_linha_com_etapa_a_mais():
    """`repeat(6, ...)` fixo era uma aposta em quantas etapas a conta tem — e as
    etapas são configuráveis. Quem criou uma 7ª (pós-venda, por exemplo) já via a
    última coluna cair pra segunda linha no desktop. Agora o quadro rola, como a
    barra de abas faz desde a reforma dela."""
    css = pp._CSS.split(".kbrow{display:grid")[1].split("}")[0]
    # o assert é na REGRA, não no arquivo: o comentário do CSS cita o número antigo
    # pra explicar o que mudou, e procurá-lo solto acusaria a própria explicação
    assert "repeat(" not in css, "voltou o número fixo de colunas"
    assert "grid-auto-columns" in css and "overflow-x:auto" in css


# ── as rotas: quem casa primeiro ───────────────────────────────────────────
def _resolve(caminho: str, metodo: str = "GET"):
    """Qual função o router escolhe pra este caminho — a mesma decisão que o
    FastAPI toma em produção, não uma leitura da ordem no arquivo."""
    from starlette.routing import Match
    escopo = {"type": "http", "method": metodo, "path": caminho,
              "path_params": {}, "root_path": "", "headers": []}
    for rota in pp.router.routes:
        if rota.matches(escopo)[0] == Match.FULL:
            return rota.endpoint.__name__
    return None


def test_regua_vem_antes_da_ficha_do_lead():
    """SUBIU QUEBRADO ASSIM. `/painel/prospeccao/{alvo_id}` é a ficha do lead, e o
    FastAPI casa por ORDEM DE REGISTRO, não por especificidade: com a ficha
    declarada primeiro, ela engolia /painel/prospeccao/regua tratando "regua" como
    id de lead, e a tela respondia 422 "unable to parse string as an integer".

    Testar a ordem das linhas no arquivo não bastaria — o que decide é o router.
    Aqui a pergunta é feita a ele."""
    assert _resolve("/painel/prospeccao/regua") == "regua_pagina"
    assert _resolve("/painel/prospeccao/regua/ritmo") == "regua_ritmo"
    assert _resolve("/painel/prospeccao/regua/config", "POST") == "regua_config"
    assert _resolve("/painel/prospeccao/regua/etapa/7", "POST") == "regua_etapa"


def test_a_ficha_do_lead_continua_atendendo_id():
    """E o conserto não pode ter roubado o caminho de quem é dono dele."""
    assert _resolve("/painel/prospeccao/629") == "prospeccao_ficha"
    assert _resolve("/painel/prospeccao/629/status", "POST") == "prospeccao_status"
    # o teto da etapa (11/09/2026) entrou com a mesma forma das rotas da ficha —
    # a pergunta é feita ao router justamente porque ler a ordem no arquivo não
    # teria pego o 422 da Régua
    assert _resolve("/painel/prospeccao/629/renovar", "POST") == "prospeccao_renovar"


# ── as duas que vieram do menu lateral (07/09/2026) ────────────────────────
def test_o_follow_up_e_o_origens_sao_abas_daqui():
    """O pedido do dono: "follow up e origens faz mais sentido dentro da aba
    prospecção, por que está tudo relacionado". A Régua já morava aqui — eram os
    dois únicos parentes ainda no menu lateral."""
    abas = _abas(pp._navbar("funil"))
    assert "📅 Follow-up" in abas and "📊 Origens" in abas
    barra = pp._navbar("funil")
    assert 'href="/painel/follow-up"' in barra and 'href="/painel/origens"' in barra


def test_cada_uma_das_duas_leva_o_proprio_portao():
    """Nem toda conta tem as duas (CLAUDE.md §6). O Follow-up só existe no perfil
    de eventos; Origens é de quem tem `caps.origens` — o vendedor não tem. Sem o
    `{% if %}` a aba apareceria e devolveria a pessoa no gate."""
    barra = pp._navbar("funil")
    assert "{% if tem_follow_up %}" in barra
    assert "{% if caps.origens and raio_x_perfil and raio_x_perfil.aplica %}" in barra


def test_o_interruptor_do_follow_up_saiu_da_regua():
    """Decisão do dono em 07/09/2026: "sai da régua e vai pra aba do follow up".

    Duas pontas, e a segunda é a que morde: além de sumir do FORMULÁRIO, o campo
    tem que sair do UPDATE. Se ficasse só no SQL, salvar a Régua — que não manda
    mais o campo — gravaria 'off' e desligaria o follow-up da conta sem ninguém
    pedir. É perda silenciosa, do tipo que a §0 do CLAUDE.md proíbe."""
    regua = _fonte("prospeccao_regua")
    assert "follow_up_modo" not in regua, "o interruptor voltou pro formulário da Régua"
    import inspect
    # a ATRIBUIÇÃO, não a palavra: o comentário que explica a ausência cita o
    # campo pelo nome, e é bom que cite.
    fonte = inspect.getsource(pp.regua_config)
    assert "follow_up_modo=" not in fonte and 'modo("follow_up_modo")' not in fonte, \
        "o UPDATE da Régua ainda escreve follow_up_modo — salvar a Régua desliga o follow-up"
    # e as outras duas continuam lá, que é o que a Régua ainda configura
    assert "gatilhos_modo" in regua and "cobranca_modo" in regua
