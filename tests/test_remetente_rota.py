"""LIBERAR UM REMETENTE TEM QUE GRAVAR — a rota, de ponta a ponta.

POR QUE ESTE ARQUIVO EXISTE. Em 21/09/2026 o dono liberou o corretor na tela e
disse que o nome apareceu na lista. `apolice_remetentes` estava VAZIA — não só na
conta dele: em TODAS as contas, desde que a lista entrou no #736. Os testes de
então cobriam `finance.apolices.liberar_remetente` (a função) e o HTML da janela
(o texto do arquivo). Ninguém tinha atravessado a rota.

Então o que se testa aqui é o caminho inteiro que o botão percorre: FormData →
rota → banco → a lista que a tela lê de volta.
"""
import os

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool
from starlette.middleware.sessions import SessionMiddleware

from finance import apolices as ap
from web import painel_apolices as pa

CONTA = 37
REF = "558694020683"


@pytest.fixture()
def cliente(monkeypatch):
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_remetente_rota"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    pool = ConnectionPool(url, min_size=1, max_size=3, open=True,
                          kwargs={"prepare_threshold": None})
    with pool.connection() as c:
        c.execute("create table contas (id bigint primary key, nome text)")
        c.execute("""create table conversas (id bigserial primary key, conta_id bigint,
                       contato_ref text, contato_nome text)""")
        c.execute("""create table mensagens (id bigserial primary key, conversa_id bigint,
                       direcao text, midia_tipo text, midia_ref jsonb, midia_meta jsonb,
                       criado_em timestamptz default now())""")
        c.execute("""create table apolice_remetentes (id bigserial primary key,
                       conta_id bigint not null references contas(id) on delete cascade,
                       contato_ref text not null, rotulo text not null default '',
                       tipo text not null default 'corretor'
                         check (tipo in ('corretor','seguradora')),
                       criado_em timestamptz not null default now(), criado_por bigint)""")
        c.execute("""create unique index ux_apolice_remetentes
                       on apolice_remetentes (conta_id, contato_ref)""")
        c.execute("insert into contas values (%s,'Liberal Neto')", (CONTA,))
        cv = c.execute("""insert into conversas (conta_id, contato_ref, contato_nome)
                          values (%s,%s,'Cássio Liberal Seguros') returning id""",
                       (CONTA, REF)).fetchone()[0]
        c.execute("""insert into mensagens (conversa_id, direcao, midia_tipo, midia_ref)
                     values (%s,'in','documento','{"mimetype":"application/pdf"}')""", (cv,))
        c.commit()

    monkeypatch.setattr(pa, "get_pool", lambda: pool)
    conta = [None] * 15
    conta[0] = CONTA
    monkeypatch.setattr(pa, "conta_logada", lambda request: tuple(conta))
    monkeypatch.setattr(pa, "nicho_da_conta", lambda conta: "seguros")

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="teste-de-sessao")
    app.include_router(pa.router)

    @app.post("/_entrar")
    async def _entrar(request: Request):
        corpo = await request.json()
        request.session["papel"] = corpo.get("papel", "dono")
        request.session["membro_id"] = corpo.get("membro_id")
        return {"ok": True}

    c = TestClient(app)
    c.pool = pool
    yield c
    pool.close()


def _entrar(c, papel="dono", membro_id=45):
    c.post("/_entrar", json={"papel": papel, "membro_id": membro_id})


def _liberados(c):
    with c.pool.connection() as cx:
        return cx.execute("select contato_ref, rotulo, tipo from apolice_remetentes "
                          " where conta_id=%s", (CONTA,)).fetchall()


# ───────────────────────────── o caminho do botão ─────────────────────────────

def test_liberar_grava_no_banco(cliente):
    """O que a tela manda é EXATAMENTE isto: FormData com acao/ref/nome/tipo."""
    _entrar(cliente)
    r = cliente.post("/painel/renovacoes/remetentes",
                     data={"acao": "liberar", "ref": REF,
                           "nome": "Cássio Liberal Seguros", "tipo": "corretor"})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}, r.text
    assert _liberados(cliente) == [(REF, "Cássio Liberal Seguros", "corretor")]


def test_o_nome_vai_junto_e_volta_na_lista(cliente):
    """O dono viu só o NÚMERO em 21/09. `remetentes()` cai pro número quando o
    rótulo está vazio, então número na tela significa rótulo vazio no banco."""
    _entrar(cliente)
    cliente.post("/painel/renovacoes/remetentes",
                 data={"acao": "liberar", "ref": REF,
                       "nome": "Cássio Liberal Seguros", "tipo": "seguradora"})
    d = cliente.get("/painel/renovacoes/remetentes").json()
    assert d["ok"] and len(d["liberados"]) == 1
    assert d["liberados"][0]["nome"] == "Cássio Liberal Seguros"
    assert d["liberados"][0]["tipo_txt"] == "Seguradora"


def test_quem_ja_esta_liberado_sai_da_lista_de_sugestao(cliente):
    _entrar(cliente)
    cliente.post("/painel/renovacoes/remetentes",
                 data={"acao": "liberar", "ref": REF, "nome": "Cássio", "tipo": "corretor"})
    d = cliente.get("/painel/renovacoes/remetentes").json()
    assert [q for q in d["mandaram"] if q["ref"] == REF][0]["liberado"] is True


def test_tirar_apaga(cliente):
    _entrar(cliente)
    cliente.post("/painel/renovacoes/remetentes",
                 data={"acao": "liberar", "ref": REF, "nome": "Cássio", "tipo": "corretor"})
    cliente.post("/painel/renovacoes/remetentes", data={"acao": "tirar", "ref": REF})
    assert _liberados(cliente) == []


def test_vendedor_nao_libera(cliente):
    """`gerencia` é dono e gestor. O vendedor recebe um não com motivo, não um 500."""
    _entrar(cliente, papel="vendedor")
    r = cliente.post("/painel/renovacoes/remetentes",
                     data={"acao": "liberar", "ref": REF, "nome": "Cássio", "tipo": "corretor"})
    assert r.json()["ok"] is False
    assert _liberados(cliente) == []


def test_numero_sem_conversa_nao_entra(cliente):
    """O cerco: sem conversa nesta conta, liberar seria digitar um id qualquer."""
    _entrar(cliente)
    r = cliente.post("/painel/renovacoes/remetentes",
                     data={"acao": "liberar", "ref": "5511999999999",
                           "nome": "Alguém", "tipo": "corretor"})
    assert r.json()["ok"] is False
    assert _liberados(cliente) == []


def test_liberar_de_novo_atualiza_em_vez_de_duplicar(cliente):
    _entrar(cliente)
    for tipo in ("corretor", "seguradora"):
        cliente.post("/painel/renovacoes/remetentes",
                     data={"acao": "liberar", "ref": REF, "nome": "Cássio", "tipo": tipo})
    assert _liberados(cliente) == [(REF, "Cássio", "seguradora")]


# ────────── de quem é o documento (21/09/2026) ──────────


def test_membro_batizado_com_o_telefone_nao_vira_numero_cru():
    """A conta 37 tem um membro chamado "86994557463": o acesso nasceu do chip e
    veio com o telefone no lugar do nome. O primeiro pré-cadastro que entrou pelo
    assistente mostrou isso na lista, e o dono reclamou que o nome não aparece."""
    from web.app import _quem_mandou

    class M:
        nome = "86994557463"
    assert _quem_mandou(M(), "5586994557463") == "(86) 99455-7463"


def test_nome_de_verdade_passa_intacto():
    from web.app import _quem_mandou

    class M:
        nome = "Cássio Liberal"
    assert _quem_mandou(M(), "5586994020683") == "Cássio Liberal"


def test_membro_sem_nome_cai_no_numero_da_conversa():
    from web.app import _quem_mandou

    class M:
        nome = ""
    assert _quem_mandou(M(), "5586994020683") == "(86) 99402-0683"
