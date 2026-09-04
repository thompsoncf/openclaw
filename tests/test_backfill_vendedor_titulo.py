"""Migração 197: recupera o vendedor que ficou faltando em título e lançamento.

Achado em produção em 04/09/2026: `fechar_orcamento` e `lancar_sinal_recebido`
não repassavam `orcamentos.criado_por` (o vendedor de quem FEZ a proposta) pro
título — mesmo o orçamento já sabendo quem era. O código está corrigido em
`finance/vendas.py`; esta migração só recupera o que já tinha acontecido antes
do conserto, copiando de uma fonte que já existe e já é confiável.
"""
import os
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

MIGRACAO = (Path(__file__).resolve().parent.parent / "db" / "migracoes"
            / "197_backfill_vendedor_titulo.sql").read_text(encoding="utf-8")

_SQL = """
create table contas (id bigserial primary key, nome text);
create table membros (id bigserial primary key, conta_id bigint, nome text);
create table orcamentos (id bigserial primary key, conta_id bigint, criado_por text);
create table lancamentos (id bigserial primary key, conta_id bigint, membro_id bigint);
create table titulos (id bigserial primary key, conta_id bigint, orcamento_id bigint,
  lancamento_id bigint, criado_por bigint);
"""


@pytest.fixture()
def pool():
    dbname = "zaq_backfill_vendedor_titulo"
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1,
                           open=True, kwargs={"autocommit": True, "prepare_threshold": None})
    with admin.connection() as c:
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity "
                  "where datname=%s and pid <> pg_backend_pid()", (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True,
                       kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        c.commit()
    yield p
    p.close()


def _rodar(pool):
    with pool.connection() as c:
        c.execute(MIGRACAO)
        c.commit()


def _titulo(pool, tid):
    with pool.connection() as c:
        return c.execute("select criado_por from titulos where id=%s", (tid,)).fetchone()[0]


def _lancamento(pool, lid):
    with pool.connection() as c:
        return c.execute("select membro_id from lancamentos where id=%s", (lid,)).fetchone()[0]


def test_preenche_titulo_e_lancamento_a_partir_do_orcamento(pool):
    """O caso da Bianca Oliveira e da Claudia Maria em produção: orçamento com
    vendedor certo, título e lançamento nascidos sem ninguém."""
    with pool.connection() as c:
        conta = c.execute("insert into contas (nome) values ('Prime') returning id").fetchone()[0]
        vend = c.execute("insert into membros (conta_id, nome) values (%s,'Jacqueline') "
                         "returning id", (conta,)).fetchone()[0]
        oid = c.execute("insert into orcamentos (conta_id, criado_por) values (%s,%s) "
                        "returning id", (conta, str(vend))).fetchone()[0]
        lid = c.execute("insert into lancamentos (conta_id, membro_id) values (%s,null) "
                        "returning id", (conta,)).fetchone()[0]
        tid = c.execute(
            "insert into titulos (conta_id, orcamento_id, lancamento_id, criado_por) "
            "values (%s,%s,%s,null) returning id", (conta, oid, lid)).fetchone()[0]
        c.commit()
    _rodar(pool)
    assert _titulo(pool, tid) == vend
    assert _lancamento(pool, lid) == vend


def test_nao_mexe_em_criado_por_ja_preenchido(pool):
    with pool.connection() as c:
        conta = c.execute("insert into contas (nome) values ('Prime') returning id").fetchone()[0]
        certo = c.execute("insert into membros (conta_id, nome) values (%s,'Ana') "
                          "returning id", (conta,)).fetchone()[0]
        outro = c.execute("insert into membros (conta_id, nome) values (%s,'Carlos') "
                          "returning id", (conta,)).fetchone()[0]
        oid = c.execute("insert into orcamentos (conta_id, criado_por) values (%s,%s) "
                        "returning id", (conta, str(outro))).fetchone()[0]
        lid = c.execute("insert into lancamentos (conta_id, membro_id) values (%s,%s) "
                        "returning id", (conta, certo)).fetchone()[0]
        tid = c.execute(
            "insert into titulos (conta_id, orcamento_id, lancamento_id, criado_por) "
            "values (%s,%s,%s,%s) returning id", (conta, oid, lid, certo)).fetchone()[0]
        c.commit()
    _rodar(pool)
    assert _titulo(pool, tid) == certo, "sobrescreveu um criado_por que já estava certo"
    assert _lancamento(pool, lid) == certo


def test_orcamento_do_dono_fica_como_esta(pool):
    """'dono' é conta sem vendedor específico — texto, não um id. Não pode virar
    um por acidente de cast, e não pode derrubar a migração inteira."""
    with pool.connection() as c:
        conta = c.execute("insert into contas (nome) values ('Prime') returning id").fetchone()[0]
        oid = c.execute("insert into orcamentos (conta_id, criado_por) values (%s,'dono') "
                        "returning id", (conta,)).fetchone()[0]
        lid = c.execute("insert into lancamentos (conta_id, membro_id) values (%s,null) "
                        "returning id", (conta,)).fetchone()[0]
        tid = c.execute(
            "insert into titulos (conta_id, orcamento_id, lancamento_id, criado_por) "
            "values (%s,%s,%s,null) returning id", (conta, oid, lid)).fetchone()[0]
        c.commit()
    _rodar(pool)
    assert _titulo(pool, tid) is None
    assert _lancamento(pool, lid) is None


def test_nao_atravessa_conta(pool):
    """O id numérico bate por coincidência com um membro de OUTRA conta — a
    migração não pode colar o vendedor errado."""
    with pool.connection() as c:
        conta = c.execute("insert into contas (nome) values ('Prime') returning id").fetchone()[0]
        outra_conta = c.execute("insert into contas (nome) values ('Vizinha') "
                                "returning id").fetchone()[0]
        vend_vizinho = c.execute("insert into membros (conta_id, nome) values (%s,'Zé') "
                                 "returning id", (outra_conta,)).fetchone()[0]
        oid = c.execute("insert into orcamentos (conta_id, criado_por) values (%s,%s) "
                        "returning id", (conta, str(vend_vizinho))).fetchone()[0]
        lid = c.execute("insert into lancamentos (conta_id, membro_id) values (%s,null) "
                        "returning id", (conta,)).fetchone()[0]
        tid = c.execute(
            "insert into titulos (conta_id, orcamento_id, lancamento_id, criado_por) "
            "values (%s,%s,%s,null) returning id", (conta, oid, lid)).fetchone()[0]
        c.commit()
    _rodar(pool)
    assert _titulo(pool, tid) is None, "colou o vendedor de outra conta"
    assert _lancamento(pool, lid) is None


def test_e_idempotente(pool):
    with pool.connection() as c:
        conta = c.execute("insert into contas (nome) values ('Prime') returning id").fetchone()[0]
        vend = c.execute("insert into membros (conta_id, nome) values (%s,'Pedro Yan') "
                         "returning id", (conta,)).fetchone()[0]
        oid = c.execute("insert into orcamentos (conta_id, criado_por) values (%s,%s) "
                        "returning id", (conta, str(vend))).fetchone()[0]
        lid = c.execute("insert into lancamentos (conta_id, membro_id) values (%s,null) "
                        "returning id", (conta,)).fetchone()[0]
        tid = c.execute(
            "insert into titulos (conta_id, orcamento_id, lancamento_id, criado_por) "
            "values (%s,%s,%s,null) returning id", (conta, oid, lid)).fetchone()[0]
        c.commit()
    _rodar(pool)
    _rodar(pool)
    assert _titulo(pool, tid) == vend
    assert _lancamento(pool, lid) == vend
