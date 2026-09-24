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


# ------------------------------------- o recorrente ganha a FORMA, não a festa
#
# Até 23/09/2026 esta seção proibia o funil em abas, a barra do celular e o
# editor sob demanda no recorrente — a ZAQ não tinha sido medida. Nesse dia o
# dono pediu, olhando a tela da ZAQ desalinhada: "deixa o mesmo modelo que já tem
# na Prime eventos do layout da página, as ordens, botões e tudo". Então a FORMA
# da tela passou a ser uma só (`.funil`), e o que continua proibido é o que é de
# FESTA: o card do evento, o plano de parcelas, Cobrar × Incluso, as categorias.

@pytest.mark.parametrize("marca", [
    'id="fn-abas"', 'id="fn-busca"', 'id="fn-vendedor"', 'id="fn-novo"',
    'id="oc-barra"', 'id="oc-voltar"', 'id="oc-buscabox"', 'id="cli-busca"',
    'id="oc-vertodos"',
])
def test_o_recorrente_tem_a_mesma_forma_da_prime(recorrente, marca):
    assert marca in recorrente


@pytest.mark.parametrize("marca", [
    'id="oc-ev-card"',      # data, convidados, tipo de festa
    'id="oc-inclusos"',     # "incluso no pacote"
    'id="pg-sem-venc"',     # aviso de parcela
    'id="pg-linhas"',       # o plano de parcelas
    'id="oc-sem-cat"',      # categorias do catálogo de festa
    'id="svc-cat"',
    'drinks, dj, buffet',   # o exemplo da busca
])
def test_nada_de_festa_vaza_pro_recorrente(recorrente, marca):
    """Seção 6: a ZAQ vende mensalidade. A forma é a mesma; o vocabulário não."""
    assert marca not in recorrente


def test_o_recorrente_continua_com_o_que_e_dele(recorrente):
    """Mensalidade, pagamento anual, os parâmetros da proposta e a IA de escopo."""
    for marca in ('id="oc-anual"', 'id="oc-r-mensal"', 'id="oc-integ"',
                  'id="oc-esc-card"', "Total 1º ano"):
        assert marca in recorrente, marca


def test_a_classe_da_forma_vale_pros_dois_e_a_da_festa_so_pro_evento(evento, recorrente):
    assert 'class="sv-wrap funil"' in recorrente
    assert 'class="sv-wrap funil evento"' in evento


def test_a_grade_antiga_do_recorrente_saiu(recorrente):
    """O cabeçalho "Custo/Margem" sobre uma coluna escondida era o que
    desalinhava a linha inteira; "Marcar todos" pertencia à lista com
    interruptor, que virou busca pra adicionar."""
    for marca in ('id="oc-head"', "Custo/Margem", 'id="oc-todos"', 'id="oc-limpar"'):
        assert marca not in recorrente, marca


# ------------------------------------------------ a folha de estilo e o script

def test_a_ordem_e_da_folha_e_vale_pela_classe_da_forma():
    css = ps._CSS_CRU
    assert ".sv-wrap.funil > #oc-funil{order:-1}" in css
    assert ".sv-wrap.funil #oc-cli-card{order:1}" in css   # cliente antes
    assert ".sv-wrap.funil #oc-ev-card{order:2}" in css    # ...do evento
    assert ".sv-wrap.funil #oc-esc-card{order:2}" in css   # ...ou do escopo
    assert ".sv-wrap.funil #ct-card{order:4}" in css       # contrato pro fim
    for regra in ("#oc-funil{order", "#oc-cli-card{order", "#ct-card{order"):
        for linha in css.splitlines():
            if regra in linha:
                assert ".sv-wrap.funil" in linha, linha


def test_a_linha_do_recorrente_tem_uma_coluna_por_caixa():
    """nome | setup | mensal | desconto | 🗑 — e o custo entra como coluna NOVA
    no Modo margem, em vez de ocupar uma que já tinha dono."""
    css = ps._CSS_CRU
    # larguras de 24/09/2026: com "R$" e centavos nos campos ("R$ 1.500,00") e o
    # alternador % | R$/mês dentro do desconto, as colunas cresceram
    assert ".oc-mod.rec{grid-template-columns:minmax(0,1fr) 112px 120px 184px auto}" in css
    assert (".sv-wrap.oc-margin .oc-mod.rec{grid-template-columns:"
            "minmax(0,1fr) 112px 120px 104px 184px auto}") in css
    js = ps._JS_CRU
    corpo = js[js.index("function buildRowRec(s)"):]
    corpo = corpo[:corpo.index("function renderCatalogoAvulso")]
    for peca in ("oc-setup", "oc-mensal", "oc-custo-col", "celDesc(", "oc-rm"):
        assert peca in corpo, peca
    # nada de festa na linha da mensalidade. O ÍCONE (svc-thumb) entrou em
    # 23/09/2026, mas com o jogo PRÓPRIO do recorrente — quem garante que nenhum
    # desenho de festa vaza pra cá é tests/test_proposta_recorrente.py.
    for peca in ("oc-cob", "oc-qtd", "Incluso"):
        assert peca not in corpo, peca


def test_a_ia_de_escopo_nao_apaga_o_orfao():
    """Regra 0: a sugestão da IA troca a seleção, mas o serviço que a proposta
    tem e o catálogo não conhece mais fica — senão o próximo Salvar apagaria."""
    js = ps._JS_CRU
    assert "Object.keys(ORFAOS).forEach(function(k){ if(antes[k]) SELECIONADOS[k]=true; });" in js


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


# ------------------------------------------------ o card do Cliente tem o salvar
# Em 23/09/2026 o dono cadastrou um cliente na proposta da ZAQ e não achou onde
# salvar: o único botão era o "Salvar no funil", no Resumo. O que ele digitou não
# chegou ao banco.

@pytest.mark.parametrize("modo", ["evento", "recorrente"])
def test_o_card_do_cliente_tem_o_proprio_salvar(modo, evento, recorrente):
    html = evento if modo == "evento" else recorrente
    form = html.split('id="cli-form-full"', 1)[1].split('class="oc-grid"', 1)[0]
    assert 'id="cli-salvar"' in form
    assert ">Salvar cliente<" in form


def test_o_salvar_do_cliente_grava_a_proposta_e_exige_o_nome():
    js = ps._JS_CRU.split("var cliSalvar=", 1)[1].split("function esc(", 1)[0]
    assert "salvarProposta(" in js       # o cliente mora na proposta
    assert "'oc-empresa'" in js          # sem nome, não grava
    assert "atualizarChip()" in js       # fecha no cartão do cliente
    assert "aditivo_url" in js           # contrato assinado: mesmo caminho do Salvar no funil
