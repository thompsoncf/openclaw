"""GET /login (e /cadastro) com sessão já autenticada: antes disso, _render()
incluía a barra lateral + banner de plano em volta do FORMULÁRIO de login —
parecia bypass de autenticação (clicar na lateral "funcionava" porque a sessão
já era válida, não porque o login foi pulado), mas confundia. Agora redireciona
pra /painel direto. Sem banco: só chama a rota com um Request fake, e a
redireção acontece ANTES de qualquer coisa que precise de DB.
"""
from web.portal import login_form, cadastro_form


class _FakeRequest:
    def __init__(self, conta_id=None, query=None):
        self.session = {"conta_id": conta_id} if conta_id else {}
        self.query_params = query or {}


def test_login_com_sessao_ativa_redireciona_pro_painel():
    r = login_form(_FakeRequest(conta_id=42))
    assert r.status_code == 303
    assert r.headers["location"] == "/painel"


def test_cadastro_com_sessao_ativa_redireciona_pro_painel():
    r = cadastro_form(_FakeRequest(conta_id=42))
    assert r.status_code == 303
    assert r.headers["location"] == "/painel"
