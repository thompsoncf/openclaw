"""Sair do app tem que sair de verdade.

O bug real: `/cockpit/sair` tirava só `cockpit`, `membro_id` e `papel` da sessão e
deixava o `conta_id` pra trás. Só que `_gerencia` lê o papel com
`get("papel", "dono")` — sem papel na sessão, o padrão é **dono**. Então o vendedor
que apertava Sair voltava pra `/cockpit` e caía na visão de GESTÃO: placar do time,
leads de todo mundo, carteira de propostas da empresa inteira e o botão de fechar
contrato. Sair virava promoção.

Não precisa de banco: o que se testa é o estado da sessão e quem os dois guards
deixam passar com esse estado.
"""
from web.painel_cockpit import _gerencia, _sessao, cockpit_sair


class _Req:
    """Request com o mínimo que os guards e o handler leem.

    `cookies` entrou junto com o "manter conectado" (migração 173): o Sair revoga o
    aparelho lembrado antes de limpar a sessão, senão o cookie reconstruiria a sessão
    no request seguinte e o botão viraria enfeite. Todo Request real do Starlette tem
    este atributo — o dublê é que estava incompleto."""
    def __init__(self, cookies=None, **sessao):
        self.session = dict(sessao)
        self.cookies = dict(cookies or {})


def _sair(**sessao):
    r = _Req(**sessao)
    resp = cockpit_sair(r)
    return r.session, resp


# ------------------------------------------------------------------ o bug em si
def test_vendedor_que_sai_nao_vira_gestao():
    sessao, resp = _sair(conta_id=7, membro_id=3, papel="vendedor", cockpit=True)
    req = _Req(**sessao)
    assert _gerencia(req) is None, "sessão vazia não pode passar como dono"
    assert _sessao(req) is None
    assert resp.status_code == 303
    assert resp.headers["location"] == "/cockpit/login"


def test_sair_limpa_a_sessao_inteira():
    """Nada sobra — nem o conta_id, que era justamente o que segurava a porta aberta."""
    sessao, _ = _sair(conta_id=7, membro_id=3, papel="vendedor", cockpit=True,
                      contextos=[{"conta_id": 7}], carrinho=["x"])
    assert sessao == {}


def test_dono_tambem_sai():
    """O dono entra pelo painel (conta_id sem membro_id) e vê a equipe. Depois de
    sair, também não vê mais."""
    req_antes = _Req(conta_id=7, papel="dono")
    assert _gerencia(req_antes) == (7, None)          # antes: entra
    sessao, _ = _sair(conta_id=7, papel="dono")
    assert _gerencia(_Req(**sessao)) is None          # depois: não


# ------------------------------------------------------------------ a raiz
def test_conta_id_sozinho_ainda_passaria_como_dono():
    """Documenta POR QUE limpar tudo é o conserto certo, e não tirar mais uma chave.

    Enquanto `_gerencia` tratar 'sem papel' como dono, qualquer resto de sessão com
    conta_id abre a visão de gestão. Se um dia esse padrão mudar, este teste falha e
    avisa que a proteção agora tem duas camadas — não que algo quebrou.
    """
    assert _gerencia(_Req(conta_id=7)) == (7, None)
