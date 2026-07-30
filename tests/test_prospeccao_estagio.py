"""Funil de 2 estágios: promoção base → lead (Novo + Quente), sem resetar a etapa
de quem já é lead, e respeitando o escopo da conta."""
import os

import pytest
from psycopg_pool import ConnectionPool

from web.painel_prospeccao import _promover_para_lead

_SQL = """
create table prospeccao (id bigserial primary key, conta_id bigint,
  estagio text not null default 'base', status text default 'novo',
  temperatura text default 'frio', atualizado_em timestamptz default now());
"""


@pytest.fixture()
def pool():
    url = os.environ["TEST_DATABASE_URL"]
    admin = ConnectionPool(url, min_size=1, max_size=1, open=True)
    db = "zaq_estagio_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {db}")
        c.execute(f"create database {db}")
    admin.close()
    turl = url.rsplit("/", 1)[0] + "/" + db
    p = ConnectionPool(turl, min_size=1, max_size=2, open=True)
    with p.connection() as c:
        c.execute(_SQL)
        c.commit()
    yield p
    p.close()


def _novo(c, **kw):
    cols = ", ".join(kw)
    vals = ", ".join(["%s"] * len(kw))
    return c.execute(f"insert into prospeccao (conta_id, {cols}) values (1, {vals}) returning id",
                     tuple(kw.values())).fetchone()[0]


def test_base_vira_lead_em_novo_e_quente(pool):
    with pool.connection() as c:
        pid = _novo(c, estagio="base", status="novo", temperatura="frio")
        c.commit()
        _promover_para_lead(c, 1, pid)
        c.commit()
        row = c.execute("select estagio, status, temperatura from prospeccao where id=%s", (pid,)).fetchone()
    assert row == ("lead", "novo", "quente")


def test_lead_existente_so_esquenta_sem_resetar_etapa(pool):
    with pool.connection() as c:
        pid = _novo(c, estagio="lead", status="proposta", temperatura="morno")
        c.commit()
        _promover_para_lead(c, 1, pid)
        c.commit()
        row = c.execute("select estagio, status, temperatura from prospeccao where id=%s", (pid,)).fetchone()
    assert row == ("lead", "proposta", "quente")   # mantém 'proposta', só vira quente


def test_promover_respeita_a_conta(pool):
    with pool.connection() as c:
        pid = _novo(c, estagio="base", status="novo")
        c.commit()
        _promover_para_lead(c, 999, pid)   # conta errada → não toca
        c.commit()
        est = c.execute("select estagio from prospeccao where id=%s", (pid,)).fetchone()[0]
    assert est == "base"
