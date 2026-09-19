"""A aba de Serviços redesenhada — e o que dela NÃO pode vazar pro outro nicho.

O redesenho de 19/09/2026 foi desenhado olhando a Prime (conta 34, eventos):
funil na frente com três abas, data da festa abrindo a linha, "Cobrar × Incluso"
no lugar do desconto de 100%, barra de total fixa no celular, Cliente antes de
Evento, contrato e aditivo no fim.

A seção 6 do CLAUDE.md existe por causa do Raio-X, que foi desenhado do mesmo
jeito e saiu falando de festa pra toda conta — a ZAQ viu "tipo de festa" e
"convidados" vazios em 43 leads que nunca terão data. Então metade deste arquivo
é sobre o que a conta RECORRENTE não pode receber.
"""
from __future__ import annotations

import jinja2
import pytest

from finance import icones_servico as ics
from web import painel_servicos as ps     # noqa: F401 — registra "servicos" no loader
from web.portal import _env


def _render(**over):
    ctx = dict(empresa_nome="MANOEL SOARES", tem_pj=True, vende_servico=True,
               servico_avulso=True, pode_contrato=True, ve_todos=True,
               tipo_padrao="pf", tipos_evento=["Casamento"],
               tipos_contrato=["Locação de espaço"], local_padrao="RUA X, 1",
               icones_paleta=ics.paleta())
    ctx.update(over)
    return _env.get_template("servicos").render(**ctx)


@pytest.fixture(scope="module")
def evento():
    return _render()


@pytest.fixture(scope="module")
def recorrente():
    return _render(servico_avulso=False, pode_contrato=False, tipo_padrao="pj",
                   tipos_evento=[], tipos_contrato=[], local_padrao="")


# ------------------------------------------------------------ o que o evento ganha

@pytest.mark.parametrize("marca", [
    'id="oc-funil"',        # o funil virou um bloco próprio, com ordem própria
    'id="fn-abas"',         # Precisa de mim / Com o cliente / Fechadas
    'id="fn-busca"',        # busca por nome, nº ou data
    'id="fn-vendedor"',     # o filtro do dono: a Prime tem três vendedores
    'id="fn-novo"',         # "+ Nova proposta" abre o editor
    'id="oc-editor"',       # o editor inteiro, que agora abre sob demanda
    'id="oc-voltar"',       # e o caminho de volta pro funil
    'id="oc-barra"',        # a barra de total do celular
    'id="oc-inclusos"',     # o resumo do que vem junto no pacote
    'id="pg-sem-venc"',     # aviso de parcela sem vencimento
    'id="oc-sem-cat"',      # aviso de serviço sem categoria
])
def test_a_tela_de_eventos_tem_as_pecas_novas(evento, marca):
    assert marca in evento


def test_o_filtro_de_vendedor_e_so_de_quem_ve_o_funil_inteiro():
    """O vendedor já vê só as propostas dele — um seletor de um nome só é ruído."""
    assert 'id="fn-vendedor"' not in _render(ve_todos=False)
    assert 'id="fn-abas"' in _render(ve_todos=False), "as abas continuam pra ele"


# ------------------------------------- o que a conta recorrente NÃO pode receber

@pytest.mark.parametrize("marca", [
    'id="fn-abas"', 'id="fn-busca"', 'id="fn-vendedor"', 'id="fn-novo"',
    'id="oc-barra"', 'id="oc-inclusos"', 'id="pg-sem-venc"',
])
def test_nada_do_evento_vaza_pro_recorrente(recorrente, marca):
    """Seção 6: a ZAQ vende mensalidade. Nem o funil em abas nem a barra de total
    do celular foram medidos nela, então ela continua com a tela de sempre."""
    assert marca not in recorrente


def test_o_recorrente_continua_com_o_editor_aberto(recorrente):
    """A ordem nova (funil na frente, editor sob demanda) vem da FOLHA, e só sob
    `.sv-wrap.evento`. Sem essa classe, a página é a de sempre."""
    assert 'class="sv-wrap"' in recorrente
    assert 'class="sv-wrap evento"' not in recorrente


def test_a_conta_de_eventos_ganha_a_classe_que_liga_a_ordem_nova(evento):
    assert 'class="sv-wrap evento"' in evento


# ------------------------------------------------ a folha de estilo e o script

def test_a_ordem_nova_e_da_folha_e_so_vale_no_evento():
    """Uma marcação só, duas ordens: é o que impede o recorrente de ser mexido
    por uma decisão tomada olhando a Prime."""
    css = ps._CSS_CRU
    assert ".sv-wrap.evento > #oc-funil{order:-1}" in css
    assert ".sv-wrap.evento #oc-cli-card{order:1}" in css   # cliente antes
    assert ".sv-wrap.evento #oc-ev-card{order:2}" in css    # ...do evento
    assert ".sv-wrap.evento #ct-card{order:4}" in css       # contrato pro fim
    # e nada disso pode valer sem a classe do nicho
    for regra in ("#oc-funil{order", "#oc-cli-card{order", "#ct-card{order"):
        for linha in css.splitlines():
            if regra in linha:
                assert ".sv-wrap.evento" in linha, linha


@pytest.mark.parametrize("fn", [
    "fnVisiveis", "fnPintarAbas", "fnPintarVendedores", "fnDesenhar",
    "abrirEditor", "fecharEditor", "incluisoDoItem",
])
def test_o_script_servido_tem_as_funcoes_novas(fn):
    """Mesma guarda dos outros blocos desta tela: a rota pode responder certo e
    a tela não desenhar nada."""
    assert fn in ps._JS_CRU


def test_a_leitura_do_incluso_na_tela_e_a_mesma_do_servidor():
    """Duas leituras de "esta linha é cobrada?" seriam dois totais — é a mesma
    razão de `finance/desconto.py` existir. Esta é a checagem possível sem rodar
    o navegador: a regra escrita no JS tem que citar os dois jeitos de dizer."""
    js = ps._JS_CRU
    assert "if(it.incluso) return true;" in js
    assert "(parseFloat(it.desc_val)||0) >= 100" in js


def test_voltar_pra_cobrar_zera_o_desconto_da_linha():
    """Sem isto, quem marcou incluso e se arrependeu receberia a linha de volta
    com 100% gravado — valendo zero e parecendo cobrada."""
    assert "if(!vira){ var di=rowc.querySelector('.oc-desc'); if(di) di.value='0'; }" \
        in ps._JS_CRU
