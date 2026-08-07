"""Editar nome e excluir colaborador da equipe (contas.equipe):

- renomear_membro corrige o nome (nome vazio não grava; o dono não muda por aqui);
- remover_membro tira o vínculo com a empresa (o dono nunca é removido).

Banco dedicado e descartável com o schema mínimo (mesmo padrão do teste de blindagem).
"""
import os

import pytest
from psycopg_pool import ConnectionPool

from contas import equipe as eq

_BASE_SQL = """
create table contas (id bigserial primary key, nome text);
create table membros (id bigserial primary key, conta_id bigint, nome text, papel text,
  email text, ativo boolean default true, convite_token text, convite_expira timestamptz,
  senha_hash text);
"""


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_equipe_edit_test"
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


def _membro(c, conta, nome, papel="vendedor"):
    return c.execute("insert into membros (conta_id, nome, papel, email) values (%s,%s,%s,%s) returning id",
                     (conta, nome, papel, f"{nome}@x.com")).fetchone()[0]


def _conta(c):
    return c.execute("insert into contas (nome) values ('Empresa') returning id").fetchone()[0]


def test_renomear_corrige_nome(pool):
    with pool.connection() as c:
        conta = _conta(c)
        m = _membro(c, conta, "Pdro")  # nome errado
        c.commit()
    assert eq.renomear_membro(pool, conta, m, "  Pedro Yann  ")["ok"] is True
    with pool.connection() as c:
        assert c.execute("select nome from membros where id=%s", (m,)).fetchone()[0] == "Pedro Yann"


def test_renomear_vazio_nao_grava(pool):
    with pool.connection() as c:
        conta = _conta(c)
        m = _membro(c, conta, "Maria")
        c.commit()
    r = eq.renomear_membro(pool, conta, m, "   ")
    assert r["ok"] is False
    with pool.connection() as c:
        assert c.execute("select nome from membros where id=%s", (m,)).fetchone()[0] == "Maria"


def test_renomear_nao_toca_dono(pool):
    with pool.connection() as c:
        conta = _conta(c)
        dono = _membro(c, conta, "Dono", papel="dono")
        c.commit()
    assert eq.renomear_membro(pool, conta, dono, "Outro")["ok"] is False
    with pool.connection() as c:
        assert c.execute("select nome from membros where id=%s", (dono,)).fetchone()[0] == "Dono"


def test_remover_tira_da_equipe(pool):
    with pool.connection() as c:
        conta = _conta(c)
        m = _membro(c, conta, "Errado")
        c.commit()
    assert eq.remover_membro(pool, conta, m)["ok"] is True
    with pool.connection() as c:
        assert c.execute("select 1 from membros where id=%s", (m,)).fetchone() is None


def test_remover_nao_toca_dono(pool):
    with pool.connection() as c:
        conta = _conta(c)
        dono = _membro(c, conta, "Dono", papel="dono")
        c.commit()
    assert eq.remover_membro(pool, conta, dono)["ok"] is False
    with pool.connection() as c:
        assert c.execute("select 1 from membros where id=%s", (dono,)).fetchone() is not None


def test_remover_escopo_por_conta(pool):
    """Não remove membro de OUTRA conta (escopo conta_id)."""
    with pool.connection() as c:
        c1, c2 = _conta(c), _conta(c)
        m = _membro(c, c2, "DeOutra")
        c.commit()
    assert eq.remover_membro(pool, c1, m)["ok"] is False   # conta errada
    with pool.connection() as c:
        assert c.execute("select 1 from membros where id=%s", (m,)).fetchone() is not None
