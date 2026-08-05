"""Regressão: aplicar na ficha um CNPJ que já pertence a OUTRO alvo da mesma conta
não pode mais dar 500 (UniqueViolation em uq_prospeccao_conta_cnpj) — tem que virar
um redirect com aviso amigável apontando pro lead que já existe.

Banco dedicado e descartável com o schema mínimo que a rota usa (mesmo padrão do
teste de blindagem) — não replica migrações antigas nem toca o banco compartilhado.
"""
import os
from types import SimpleNamespace

import pytest
from psycopg_pool import ConnectionPool
from starlette.responses import RedirectResponse

from web import painel_prospeccao as pp

_BASE_SQL = """
create table prospeccao (id bigserial primary key, conta_id bigint, empresa text,
  cnpj text, estagio text default 'base', vendedor_id bigint, atualizado_em timestamptz default now(),
  constraint uq_prospeccao_conta_cnpj unique (conta_id, cnpj));
create table campanhas (id bigserial primary key, nome text);
create table campanha_alvos (id bigserial primary key, prospeccao_id bigint,
  campanha_id bigint, criado_em timestamptz default now());
"""


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_aplicar_cnpj_dup_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_BASE_SQL)
        c.commit()
    yield p
    p.close()


def test_aplicar_cnpj_ja_em_outro_alvo_nao_da_500(pool, monkeypatch):
    conta = 3
    with pool.connection() as c:
        # lead A já tem o CNPJ; lead B é o alvo onde vamos (por engano) aplicar o mesmo
        a = c.execute("insert into prospeccao (conta_id, empresa, cnpj, estagio) "
                      "values (%s,'Ferronorte','10320422000106','lead') returning id", (conta,)).fetchone()[0]
        b = c.execute("insert into prospeccao (conta_id, empresa, cnpj) "
                      "values (%s,'Outra Empresa',null) returning id", (conta,)).fetchone()[0]
        c.commit()

    alvo_b = {"id": b, "empresa": "Outra Empresa", "cnpj": None, "vendedor_id": None}
    monkeypatch.setattr(pp, "get_pool", lambda: pool)
    monkeypatch.setattr(pp, "_acesso", lambda req: ({"conta_id": conta, "membro_id": 1,
                                                     "gerencia": True, "pode_atribuir": True}, None))
    monkeypatch.setattr(pp, "_carrega_alvo", lambda pool_, cid, aid: alvo_b)
    monkeypatch.setattr(pp, "_pode_ver", lambda alvo, ctx: True)
    # não bate na CNPJá; força o caminho simples de update (o enriquecimento não importa aqui)
    monkeypatch.setattr(pp.fontes, "enriquecer_cnpj", lambda limpo: {"ok": False})

    req = SimpleNamespace(session={}, headers={})
    resp = pp.prospeccao_aplicar_cnpj(req, alvo_id=b, cnpj="10.320.422/0001-06")

    assert isinstance(resp, RedirectResponse)
    assert resp.status_code == 303
    aviso = req.session.get("prosp_aviso", "")
    assert "já está em outro alvo" in aviso
    assert "Ferronorte" in aviso            # aponta pro lead que já tem o CNPJ

    # e o alvo B continua sem CNPJ (a transação abortada não gravou nada nele)
    with pool.connection() as c:
        assert c.execute("select cnpj from prospeccao where id=%s", (b,)).fetchone()[0] is None
        # o lead A segue intacto
        assert c.execute("select cnpj from prospeccao where id=%s", (a,)).fetchone()[0] == "10320422000106"


def test_aplicar_cnpj_novo_grava_limpo(pool, monkeypatch):
    """Caminho feliz: CNPJ inédito na conta é gravado só com dígitos (o que a constraint usa)."""
    conta = 7
    with pool.connection() as c:
        b = c.execute("insert into prospeccao (conta_id, empresa, cnpj) "
                      "values (%s,'Empresa Nova',null) returning id", (conta,)).fetchone()[0]
        c.commit()

    alvo_b = {"id": b, "empresa": "Empresa Nova", "cnpj": None, "vendedor_id": None}
    monkeypatch.setattr(pp, "get_pool", lambda: pool)
    monkeypatch.setattr(pp, "_acesso", lambda req: ({"conta_id": conta, "membro_id": 1,
                                                     "gerencia": True, "pode_atribuir": True}, None))
    monkeypatch.setattr(pp, "_carrega_alvo", lambda pool_, cid, aid: alvo_b)
    monkeypatch.setattr(pp, "_pode_ver", lambda alvo, ctx: True)
    monkeypatch.setattr(pp.fontes, "enriquecer_cnpj", lambda limpo: {"ok": False})

    req = SimpleNamespace(session={}, headers={})
    pp.prospeccao_aplicar_cnpj(req, alvo_id=b, cnpj="11.222.333/0001-81")

    with pool.connection() as c:
        assert c.execute("select cnpj from prospeccao where id=%s", (b,)).fetchone()[0] == "11222333000181"
