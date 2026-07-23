"""Blindagem: aplicar_migracoes roda as pendentes no deploy, é idempotente e
serializado por advisory lock (não quebra se dois processos subirem juntos).

Simula o cenário de PRODUÇÃO: banco com as tabelas base mas SEM as colunas novas,
migrações antigas já marcadas como aplicadas, só 088/089 pendentes.
"""
import os
import glob

import pytest
from psycopg_pool import ConnectionPool

from db.aplicar_migracoes import aplicar_migracoes

# tabelas base mínimas que as migrações 088/089 referenciam (FKs)
_BASE_SQL = """
create table contas (id bigserial primary key, tipo text, nome text);
create table membros (id bigserial primary key, conta_id bigint, nome text);
create table lancamentos (id bigserial primary key, conta_id bigint references contas(id),
  membro_id bigint, tipo text, valor_centavos bigint, categoria text, descricao text default '',
  data date, pagamento text default '', origem text default 'manual', comprovante text default '',
  chave varchar(44), natureza text, criado_em timestamptz default now());
create table funcionarios (id bigserial primary key, conta_id bigint references contas(id),
  nome text, salario_centavos int default 0, pro_labore boolean default false, ativo boolean default true);
create table schema_migrations (id serial primary key, nome text unique not null,
  executada_em timestamptz default now());
"""


@pytest.fixture()
def pool():
    url = os.environ["TEST_DATABASE_URL"]
    # banco dedicado e descartável pra este teste (não colide com outros)
    admin = ConnectionPool(url, min_size=1, max_size=1, open=True)
    dbname = "zaq_blindagem_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    test_url = url.rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(test_url, min_size=1, max_size=3, open=True)
    with p.connection() as c:
        c.execute(_BASE_SQL)
        c.commit()
    yield p
    p.close()


def _baseline_ate_087(pool):
    """Marca todas as migrações < 088 como já aplicadas (como no prod real)."""
    nomes = sorted(os.path.basename(f) for f in glob.glob("db/migracoes/*.sql"))
    antigas = [n for n in nomes if n < "088"]
    with pool.connection() as c:
        for n in antigas:
            c.execute("insert into schema_migrations(nome) values(%s) on conflict do nothing", (n,))
        c.commit()
    return antigas


def _col_existe(pool, tabela, coluna):
    with pool.connection() as c:
        r = c.execute(
            "select 1 from information_schema.columns where table_name=%s and column_name=%s",
            (tabela, coluna)).fetchone()
    return r is not None


def _tabela_existe(pool, tabela):
    with pool.connection() as c:
        r = c.execute(
            "select 1 from information_schema.tables where table_name=%s", (tabela,)).fetchone()
    return r is not None


def test_aplica_so_pendentes_e_cria_colunas(pool):
    _baseline_ate_087(pool)
    # antes: colunas novas não existem
    assert not _col_existe(pool, "lancamentos", "forma_pagamento")
    assert not _col_existe(pool, "funcionarios", "vale_transporte")

    n = aplicar_migracoes(pool)
    assert n == 2  # só 088 e 089 estavam pendentes

    # depois: colunas/tabela criadas
    assert _col_existe(pool, "lancamentos", "forma_pagamento")
    assert _tabela_existe(pool, "parcelas_cartao")
    assert _col_existe(pool, "funcionarios", "vale_transporte")


def test_idempotente_segunda_rodada_nao_aplica_nada(pool):
    _baseline_ate_087(pool)
    assert aplicar_migracoes(pool) == 2
    # rodar de novo (ex.: outro deploy/instância) não aplica nada e não quebra
    assert aplicar_migracoes(pool) == 0
