"""TODO link do menu tem que concordar com o gate — não só o da Agenda.

Já existe `test_menu_agenda_por_papel.py`, escrito depois do #490 porque a rota
da Agenda abria e nenhum link aparecia. Ele fixa uma tela só, e por isso não
pegou o erro seguinte, que foi o espelho: no #650 a tela **Origens** ganhou link
no menu para dono e gestor, mas `/painel/origens` não entrou no `rotas_do_papel`.
O dono não sentiu (o gate pula o titular); o gestor clicava e levava 303 de volta
pro `/painel/servicos`.

As duas falhas são a mesma classe — menu e whitelist saindo de lugares diferentes
— e nenhuma delas dá erro: uma esconde o link, a outra devolve a pessoa. Por isso
este arquivo não fala de nenhuma tela em particular: ele lê os links que o
template `base` realmente renderiza para cada papel e cobra concordância com
`contas.equipe.rotas_do_papel`.

Sem banco: renderiza o `base` direto, que é onde o menu mora.
"""
import re

import pytest

# importar os módulos registra os templates deles no _env (o `base` é do portal,
# mas o menu cita telas que só existem quando o módulo foi importado)
import web.painel_agenda  # noqa: F401
import web.painel_conteudo  # noqa: F401
import web.painel_origens  # noqa: F401
import web.painel_raio_x  # noqa: F401
from contas import equipe as eq
from web.portal import _env

#: conta PJ com app, na forma que o template lê (índice 4 = plano, 11 = tem_pj,
#: 16 = slug do nicho, que o #647 pôs no fim da tupla)
_CONTA_PJ = [1, "pj", "Buffet", "doc", "app_pro", None, None, None,
             False, None, None, True, None, None, True, None, "eventos"]

#: papéis de MEMBRO — o dono passa em tudo e é conferido à parte
_MEMBROS = ["gestor", "vendedor", "financeiro", "restrito", "membro", "convidado"]

#: rotas que o gate deixa passar pra qualquer um, e que por isso não precisam
#: aparecer no menu (nem podem ser cobradas dele)
_LIVRES = {"/trocar", "/sair", "/painel/versao"}


def _menu(papel: str) -> str:
    perfil = {"chave": "eventos", "aplica": True,
              "vocab": {"data": True, "compromisso": "visita"}}
    return _env.get_template("base").render(
        logado=True, papel=papel, caps=eq.caps_do_papel(papel), conta=_CONTA_PJ,
        tem_pj=True, vende_servico=True, vende_produto=False, secao_ativa="",
        n_contextos=1, ve_novidades=eq.recebe_novidades(papel), novidades_n=0,
        tem_cesta=False,
        embed=False, raio_x_perfil=perfil)


def _links_do_menu(papel: str) -> set[str]:
    """Os destinos `/painel/...` que o menu oferece a este papel."""
    achados = set(re.findall(r'href="(/painel/[a-z0-9\-/]*)"', _menu(papel)))
    return {a.rstrip("/") for a in achados} - _LIVRES


def _passa_no_gate(papel: str, rota: str) -> bool:
    """A mesma pergunta que `web/app.py:_gate_permissoes` faz."""
    return any(rota == a or rota.startswith(a + "/")
               for a in eq.rotas_do_papel(papel))


# ── a rede que faltava ─────────────────────────────────────────────────────

@pytest.mark.parametrize("papel", _MEMBROS)
def test_todo_link_do_menu_passa_no_gate(papel):
    """Link que o gate devolve é pior que link ausente: a pessoa clica, some da
    tela onde estava e não recebe explicação nenhuma."""
    barrados = sorted(r for r in _links_do_menu(papel) if not _passa_no_gate(papel, r))
    assert not barrados, (
        f"o menu de '{papel}' oferece {barrados}, e o gate devolve 303 em cada um. "
        f"Falta liberar em contas.equipe.rotas_do_papel."
    )


@pytest.mark.parametrize("papel", _MEMBROS)
def test_toda_rota_liberada_tem_link_no_menu(papel):
    """A outra ponta, que é o erro do #490: permissão sem link é tela que só quem
    souber a URL alcança."""
    menu = _links_do_menu(papel)
    sem_link = sorted(r for r in eq.rotas_do_papel(papel)
                      if r.startswith("/painel/") and r not in _LIVRES
                      and not any(m == r or m.startswith(r + "/") for m in menu))
    assert not sem_link, (
        f"'{papel}' pode abrir {sem_link}, mas o menu não oferece nenhum caminho. "
        f"Falta o `navi(...)` no template `base`."
    )


def test_o_dono_ve_no_menu_tudo_que_o_gestor_ve():
    """O dono não usa a whitelist (o gate o ignora), então a checagem dele é
    contra o gestor: se uma tela aparece pro gestor e some pro dono, alguém
    prendeu o link num `{% if %}` errado."""
    faltando = _links_do_menu("gestor") - _links_do_menu("dono")
    assert not faltando, f"o dono não vê no menu: {sorted(faltando)}"


def test_origens_e_o_caso_que_motivou_este_arquivo():
    """Nomeado de propósito: quando este quebrar, é o #650 acontecendo de novo."""
    assert _passa_no_gate("gestor", "/painel/origens"), \
        "o gestor vê 'Origens' no menu desde o #650 e o gate o devolve"
    assert "/painel/origens" in _links_do_menu("gestor")
