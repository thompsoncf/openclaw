"""O papel de convidado: a agência entra, vê Origens, e nada mais.

Este arquivo é o contrato de PRIVACIDADE do acordo com a Prime. O convidado é
gente de FORA da empresa — a agência de tráfego — e o que ele alcança é o que o
dono prometeu que ele alcançaria. Cada rota barrada aqui é uma promessa: conversa
de cliente, agenda, caixa, funil e equipe não são assunto dele.

Duas armadilhas que este arquivo existe pra travar:

* **O gate não guarda o Cockpit.** `web/app.py` só barra `/painel*` e `/membros*`;
  o Cockpit tem gate próprio (`finance/cockpit._PAPEIS_OK`). Hoje o convidado fica
  de fora porque não está naquela tupla — o que é sorte de desenho, não garantia.
* **O laço de redirect.** Papel novo cujo destino não está na whitelist manda a
  pessoa pra uma tela que o gate devolve. O `home_do_papel` e o `home` do gate
  precisam apontar pra algo que ele possa abrir.
"""
import pytest

from contas import equipe as eq
from finance import cockpit as ck

#: tudo que a agência NÃO pode alcançar. Cada linha é uma promessa ao cliente.
FECHADAS = [
    "/painel",              # o painel do dono: plano, pessoas da conta
    "/painel/prospeccao",   # funil, ficha do lead, conversa de cliente
    "/painel/relatorios",   # faturamento, comissão, contas a pagar
    "/painel/empresa",      # dados cadastrais, caixa
    "/painel/agenda",       # a agenda da casa
    "/painel/equipe",       # quem trabalha na empresa
    "/painel/raio-x",       # o placar interno dos vendedores
    "/painel/follow-up",    # a fila de quem precisa ser cobrado
    "/painel/servicos",
    "/membros",
]


def _passa(papel, rota):
    """A mesma pergunta do gate em web/app.py."""
    return any(rota == a or rota.startswith(a + "/")
               for a in eq.rotas_do_papel(papel))


# ── o que ele abre ─────────────────────────────────────────────────────────

def test_convidado_abre_origens():
    assert _passa("convidado", "/painel/origens")


def test_a_casa_dele_e_a_tela_dele():
    assert eq.home_do_papel("convidado", membro_id=1) == "/painel/origens"


def test_a_casa_dele_passa_no_gate():
    """Senão é laço: o destino do login volta 303 e a pessoa nunca entra."""
    assert _passa("convidado", eq.home_do_papel("convidado", membro_id=1))


# ── o que ele NÃO abre ─────────────────────────────────────────────────────

@pytest.mark.parametrize("rota", FECHADAS)
def test_convidado_nao_alcanca(rota):
    assert not _passa("convidado", rota), f"o convidado alcança {rota}"


def test_convidado_nao_entra_no_cockpit():
    """O Cockpit não passa pelo gate do painel — tem portão próprio. Se alguém
    acrescentar 'convidado' àquela tupla, a agência lê conversa de cliente."""
    assert "convidado" not in ck._PAPEIS_OK


def test_convidado_nao_recebe_novidades():
    """As Novidades contam mudanças internas do produto, com nome de tela e de
    fluxo. Não é comunicação pra fornecedor."""
    assert not eq.recebe_novidades("convidado")


def test_convidado_nao_gere_nem_vende_nem_ve_financeiro():
    caps = eq.caps_do_papel("convidado")
    assert caps == {"vendas": False, "financeiro": False, "gerir": False,
                    "origens": True}


# ── o papel existe pra ser convidado ───────────────────────────────────────

def test_o_dono_pode_atribuir_o_papel():
    """Sem estar em PAPEIS_PJ o papel existe no código e não aparece na tela de
    Equipe — ou seja, ninguém consegue criar um convidado."""
    assert "convidado" in eq.PAPEIS_PJ


def test_o_papel_tem_rotulo_que_explica_o_que_e():
    assert eq.rotulo("convidado") == "Convidado (agência)"


# ── a capacidade nova não vazou pra ninguém ────────────────────────────────

@pytest.mark.parametrize("papel", ["vendedor", "financeiro", "membro", "restrito"])
def test_origens_nao_vazou_pros_outros_papeis(papel):
    assert not eq.caps_do_papel(papel)["origens"]
    assert not _passa(papel, "/painel/origens")


@pytest.mark.parametrize("papel", ["dono", "gestor"])
def test_quem_manda_na_conta_continua_vendo_origens(papel):
    assert eq.caps_do_papel(papel)["origens"]


def test_papel_desconhecido_nao_ganha_origens():
    """`_SEM_ACESSO` é o padrão de quem não tem papel — errar pra esse lado abre
    a tela pra quem não devia."""
    assert not eq.caps_do_papel("papel_que_nao_existe")["origens"]
    assert not eq.caps_do_papel(None)["origens"]


# ── o destino de quem é barrado ────────────────────────────────────────────
# Este bloco existe porque uma mutação passou: tirar o ramo do convidado do
# destino do gate não quebrava teste nenhum. A causa era haver DUAS contas do
# mesmo destino — uma em contas/equipe e outra escrita à mão em web/app.py.
# Agora é uma só, e é esta que os testes cobram.

@pytest.mark.parametrize("papel", ["gestor", "vendedor", "financeiro",
                                   "convidado", "restrito", "membro"])
def test_destino_de_quem_e_barrado_passa_no_gate(papel):
    """Senão o gate devolve o próprio destino e a pessoa roda em círculo."""
    destino = eq.destino_barrado(papel)
    assert destino == "/trocar" or _passa(papel, destino), \
        f"{papel} é mandado pra {destino}, que o gate devolve"


def test_convidado_barrado_vai_pra_tela_dele_e_nao_pro_trocar():
    """Cair no /trocar é tecnicamente seguro e péssimo na prática: a agência
    aterrissa numa tela de trocar de empresa sem entender o que aconteceu."""
    assert eq.destino_barrado("convidado") == "/painel/origens"


def test_o_gate_usa_essa_funcao_e_nao_uma_copia():
    """A cópia à mão em web/app.py foi o que deixou o convidado no /trocar."""
    import inspect
    import web.app as app
    fonte = inspect.getsource(app._gate_permissoes)
    assert "_destino_barrado(papel)" in fonte
    assert "/painel/servicos" not in fonte, "voltou a decidir o destino aqui"
