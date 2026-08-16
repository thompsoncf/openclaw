"""Apagar uma proposta do funil — e as duas que NÃO podem ser apagadas.

Até agora o funil só crescia: proposta gerada errada ficava lá pra sempre,
contando nos números e aparecendo pro vendedor. Isso passou a doer de verdade
quando o agente começou a gerar orçamento sozinho no meio da conversa — um "quanto
custa?" mal interpretado vira documento no funil, e não havia botão nenhum.

O que estes testes prendem não é o `delete`; é o que ele se recusa a fazer:

* PROPOSTA ASSINADA NÃO SOME. `aprovada` tem aceite do cliente; `fechado` já virou
  título a receber no módulo Empresa. Apagar deixaria o financeiro apontando pro
  nada. Erro em documento assinado se conserta emitindo outro.
* VENDEDOR NÃO APAGA A DOS OUTROS. O id vem da tela, e tela não é fonte confiável:
  quem só enxerga as próprias propostas na listagem tem que esbarrar no servidor
  se tentar o id do colega.
* E O LEAD NÃO CAI JUNTO. `prospeccao.orcamento_id` é FK sem `on delete`, então o
  delete cru estouraria justamente no caso mais comum — o orçamento que nasceu de
  um lead. O vínculo se solta, o lead fica.

Banco dedicado e descartável, no padrão dos outros testes de funil.
"""
import os

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool
from starlette.middleware.sessions import SessionMiddleware

from web import painel_servicos as ps

CONTA = 7


@pytest.fixture()
def cliente(monkeypatch):
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_orc_excluir"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    pool = ConnectionPool(url, min_size=1, max_size=3, open=True,
                          kwargs={"prepare_threshold": None})
    with pool.connection() as c:
        c.execute("create table contas (id bigserial primary key, nome text)")
        c.commit()
    with pool.connection() as c:
        ps._garantir_tabela(c)          # cria orcamentos como em produção
    with pool.connection() as c:
        c.execute("""create table prospeccao (id bigserial primary key, conta_id bigint,
                       empresa text, orcamento_id bigint references orcamentos(id),
                       criado_em timestamptz default now())""")
        c.execute("insert into contas (id, nome) values (%s,'Empresa Teste')", (CONTA,))
        c.commit()

    monkeypatch.setattr(ps, "get_pool", lambda: pool)
    # conta_logada devolve a tupla do portal; só três posições importam pro gate.
    conta = [None] * 15
    conta[0], conta[11], conta[12], conta[14] = CONTA, True, True, True
    monkeypatch.setattr(ps, "conta_logada", lambda request: tuple(conta))

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="teste-de-sessao")
    app.include_router(ps.router)

    @app.post("/_entrar")                 # só pro teste: carimba quem está logado
    async def _entrar(request: Request):
        corpo = await request.json()
        request.session["papel"] = corpo.get("papel", "dono")
        request.session["membro_id"] = corpo.get("membro_id")
        return {"ok": True}

    c = TestClient(app)
    c.pool = pool
    yield c
    pool.close()


def _entrar(c, papel="dono", membro_id=None):
    c.post("/_entrar", json={"papel": papel, "membro_id": membro_id})


def _proposta(c, *, status="rascunho", criado_por="", cliente_nome="Thompson") -> int:
    with c.pool.connection() as cx:
        r = cx.execute(
            """insert into orcamentos (conta_id, cliente, empresa, status, criado_por,
                 setup_centavos, modo)
               values (%s,%s,%s,%s,%s,1192000,'evento') returning id""",
            (CONTA, cliente_nome, cliente_nome, status, criado_por)).fetchone()
        cx.commit()
        return r[0]


def _existe(c, oid) -> bool:
    with c.pool.connection() as cx:
        return cx.execute("select 1 from orcamentos where id=%s", (oid,)).fetchone() is not None


# ------------------------------------------------------------ o caminho normal

def test_dono_apaga_rascunho(cliente):
    _entrar(cliente)
    oid = _proposta(cliente)
    r = cliente.post("/painel/servicos/excluir", json={"id": oid})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert not _existe(cliente, oid)


def test_proposta_de_outra_conta_nao_existe_pra_mim(cliente):
    """Escopo por conta: id de outra empresa responde 404, não apaga."""
    _entrar(cliente)
    with cliente.pool.connection() as cx:
        cx.execute("insert into contas (id, nome) values (99,'Outra')")
        oid = cx.execute("""insert into orcamentos (conta_id, cliente, status)
                            values (99,'Alheio','rascunho') returning id""").fetchone()[0]
        cx.commit()
    r = cliente.post("/painel/servicos/excluir", json={"id": oid})
    assert r.status_code == 404
    assert _existe(cliente, oid)


def test_id_que_nao_existe(cliente):
    _entrar(cliente)
    assert cliente.post("/painel/servicos/excluir", json={"id": 987654}).status_code == 404


# ------------------------------------------------- documento assinado não some

@pytest.mark.parametrize("status", ["aprovada", "fechado"])
def test_assinada_e_fechada_resistem(cliente, status):
    _entrar(cliente)
    oid = _proposta(cliente, status=status)
    r = cliente.post("/painel/servicos/excluir", json={"id": oid})
    assert r.status_code == 409
    assert "assinada" in r.json()["erro"]
    assert _existe(cliente, oid)


@pytest.mark.parametrize("status", ["rascunho", "enviado", "negociando", "perdido"])
def test_os_outros_estados_apagam(cliente, status):
    """Perdido também sai: é o estado de quem quer limpar o funil."""
    _entrar(cliente)
    oid = _proposta(cliente, status=status)
    assert cliente.post("/painel/servicos/excluir", json={"id": oid}).status_code == 200
    assert not _existe(cliente, oid)


# --------------------------------------------------- vendedor só mexe no dele

def test_vendedor_apaga_a_propria(cliente):
    _entrar(cliente, papel="vendedor", membro_id=42)
    oid = _proposta(cliente, criado_por="42")
    assert cliente.post("/painel/servicos/excluir", json={"id": oid}).status_code == 200
    assert not _existe(cliente, oid)


def test_vendedor_nao_apaga_a_do_colega(cliente):
    """O id vem da tela; o servidor é quem tem que dizer não."""
    _entrar(cliente, papel="vendedor", membro_id=42)
    oid = _proposta(cliente, criado_por="7")
    r = cliente.post("/painel/servicos/excluir", json={"id": oid})
    assert r.status_code == 403
    assert _existe(cliente, oid)


def test_dono_apaga_a_do_vendedor(cliente):
    """Quem enxerga o funil inteiro manda no funil inteiro."""
    _entrar(cliente, papel="dono")
    oid = _proposta(cliente, criado_por="42")
    assert cliente.post("/painel/servicos/excluir", json={"id": oid}).status_code == 200


# --------------------------------------------------------- o lead fica de pé

def test_lead_vinculado_nao_impede_e_nao_some(cliente):
    """A FK prospeccao.orcamento_id derrubaria o delete; o vínculo se solta e o
    lead continua no funil de prospecção, que é o dado de verdade."""
    _entrar(cliente)
    oid = _proposta(cliente)
    with cliente.pool.connection() as cx:
        lead = cx.execute("""insert into prospeccao (conta_id, empresa, orcamento_id)
                             values (%s,'Prime',%s) returning id""", (CONTA, oid)).fetchone()[0]
        cx.commit()
    assert cliente.post("/painel/servicos/excluir", json={"id": oid}).status_code == 200
    with cliente.pool.connection() as cx:
        r = cx.execute("select orcamento_id from prospeccao where id=%s", (lead,)).fetchone()
    assert r is not None and r[0] is None
