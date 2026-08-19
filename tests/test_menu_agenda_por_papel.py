"""O link da Agenda no menu tem que existir pra quem o gate deixa entrar.

O buraco que este arquivo fecha: o PR da agenda compartilhada (#490) liberou
`/painel/agenda` no `rotas_do_papel`, mas o link do menu continuou preso a
`{% if _dono %}` — dentro do grupo "Pessoal", de quando a agenda era só do dono. A
rota abria e NENHUM link aparecia pro time: o vendedor só chegaria digitando a URL.

O teste do #490 conferia a PERMISSÃO (`rotas_do_papel`) e passou verde com o menu
quebrado. Por isso este mede a outra ponta — o HTML renderizado — e amarra as duas:
quem o gate deixa entrar tem que ter link, e quem ele barra não pode ter.

Sem banco: renderiza o template `base` direto, que é onde o menu mora.
"""
import pytest

import web.painel_agenda  # noqa: F401 — registra os templates do módulo no _env
import web.painel_conteudo  # noqa: F401
from contas import equipe as eq
from web.portal import _env

# conta PJ com app: a forma que o template lê (conta[4] = plano, conta[11] = tem_pj)
_CONTA_PJ = [1, "pj", "Buffet", "doc", "app_pro", None, None, None,
             False, None, None, True, None, None, True]
# conta só de cesta: `_tem_app` falso — não pode ganhar menu que nunca teve
_CONTA_CESTA = [2, "pf", "Casa", "doc", "zaq_cesta", None, None, None,
                False, None, True, False, None, None, False]


def _menu(papel: str, conta=None) -> str:
    return _env.get_template("base").render(
        logado=True, papel=papel, caps=eq.caps_do_papel(papel),
        conta=conta if conta is not None else _CONTA_PJ,
        tem_pj=bool((conta or _CONTA_PJ)[11]), vende_servico=True, vende_produto=False,
        secao_ativa="", n_contextos=1, ve_novidades=False, novidades_n=0,
        tem_cesta=False, embed=False)


def _pode_entrar(papel: str) -> bool:
    return "/painel/agenda" in eq.rotas_do_papel(papel)


# ── as duas pontas amarradas ───────────────────────────────────────────────
@pytest.mark.parametrize("papel", ["dono", "gestor", "vendedor", "financeiro"])
def test_quem_o_gate_deixa_entrar_tem_link_no_menu(papel):
    assert "/painel/agenda" in _menu(papel), \
        f"{papel} pode abrir a agenda mas não tem link nenhum pra ela"


def test_o_dono_passa_no_gate_por_ser_dono_e_tambem_tem_link():
    """O dono não usa `rotas_do_papel` (passa em tudo), então ele é conferido à parte —
    e o risco real era mover a Agenda de grupo e sumir com ela pra ELE."""
    assert "/painel/agenda" in _menu("dono")


@pytest.mark.parametrize("papel", ["gestor", "vendedor", "financeiro", "restrito"])
def test_menu_e_gate_dizem_a_mesma_coisa(papel):
    """A trava que teria pego o bug: link e permissão têm que concordar, sempre."""
    assert ("/painel/agenda" in _menu(papel)) is _pode_entrar(papel), \
        f"menu e gate discordam pra {papel}"


def test_restrito_nao_ve_a_agenda():
    assert "/painel/agenda" not in _menu("restrito")


# ── o que não pode mudar ───────────────────────────────────────────────────
def test_conta_so_de_cesta_nao_ganha_agenda():
    """`_tem_app` continua na condição: quem só usa a cesta nunca teve esse menu e
    não pode passar a ter porque a agenda virou compartilhada."""
    assert "/painel/agenda" not in _menu("dono", conta=_CONTA_CESTA)


def test_a_agenda_saiu_do_grupo_pessoal():
    """Ela era item 'Pessoal', ao lado de Painel e Lista de compras — de quando era a
    agenda particular do dono. Agora é ferramenta de trabalho do time; se voltar pra
    lá, volta a ficar escondida de quem não é dono."""
    from web import portal
    pessoal = portal._BASE.split("Pessoal</div>")[1].split("{% endif %}")[1]
    assert "/painel/agenda" not in pessoal[:400]
