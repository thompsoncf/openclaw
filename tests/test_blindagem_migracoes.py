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
create table folha_eventos (id bigserial primary key, conta_id bigint references contas(id),
  funcionario_id bigint references funcionarios(id),
  tipo text not null check (tipo in ('vale','extra','desconto','pagamento')),
  valor_centavos int, competencia date, descricao text default '', lancamento_id bigint,
  criado_em timestamptz default now());
create table nichos (id bigserial primary key, nome text, slug text unique, tipo text,
  ativo boolean not null default true);
-- canais_config vem da 081 (marcada como aplicada no baseline); a 096 a altera,
-- então precisa existir aqui. Colunas mínimas que a 096 assume (+ token da 084).
create table canais_config (id bigserial primary key, conta_id bigint, canal text,
  identificador text, ativo boolean default true, token text);
-- prospeccao vem da 075 (marcada como aplicada); a 102 adiciona colunas de decisor.
create table prospeccao (id bigserial primary key, conta_id bigint);
-- campanhas vem da 086 (marcada como aplicada); a 104/105 adicionam colunas.
create table campanhas (id bigserial primary key, conta_id bigint);
-- campanha_alvos vem da 086; a 105 adiciona wa_status/wa_em.
create table campanha_alvos (id bigserial primary key, campanha_id bigint, prospeccao_id bigint);
create table schema_migrations (id serial primary key, nome text unique not null,
  executada_em timestamptz default now());
"""


def _pendentes_a_partir_de(corte: str) -> list[str]:
    """Nomes das migrações >= corte (as que ficam pendentes após o baseline)."""
    nomes = sorted(os.path.basename(f) for f in glob.glob("db/migracoes/*.sql"))
    return [n for n in nomes if n >= corte]


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

    esperado = len(_pendentes_a_partir_de("088"))  # 088, 089, 090, ...
    n = aplicar_migracoes(pool)
    assert n == esperado  # aplicou exatamente as pendentes

    # depois: colunas/tabela das migrações sob teste foram criadas
    assert _col_existe(pool, "lancamentos", "forma_pagamento")   # 088
    assert _tabela_existe(pool, "parcelas_cartao")               # 088
    assert _col_existe(pool, "funcionarios", "vale_transporte")  # 089
    assert _col_existe(pool, "contas", "cnae")                   # 090


def test_idempotente_segunda_rodada_nao_aplica_nada(pool):
    _baseline_ate_087(pool)
    assert aplicar_migracoes(pool) == len(_pendentes_a_partir_de("088"))
    # rodar de novo (ex.: outro deploy/instância) não aplica nada e não quebra
    assert aplicar_migracoes(pool) == 0


def test_lock_nao_bloqueia_quando_ja_ocupado(pool):
    """Com o lock preso por outra sessão, _obter_lock NÃO bloqueia (evita o
    'canceling statement due to statement timeout' dos dois preDeploy do Render):
    tenta poucas vezes e desiste."""
    from db import aplicar_migracoes as am
    with pool.connection() as dono:
        dono.execute("select pg_advisory_lock(%s)", (am._LOCK_MIGRACOES,))
        dono.commit()
        try:
            with pool.connection() as outra:
                assert am._obter_lock(outra, tentativas=2, espera=0.05) is False
        finally:
            dono.execute("select pg_advisory_unlock(%s)", (am._LOCK_MIGRACOES,))
            dono.commit()


def test_segue_e_aplica_mesmo_sem_o_lock(pool, monkeypatch):
    """Se o lock não vier (outra instância migrando/lock vazado), as migrações
    ainda são aplicadas — são idempotentes e o Postgres serializa o DDL."""
    from db import aplicar_migracoes as am
    _baseline_ate_087(pool)
    monkeypatch.setattr(am, "_obter_lock", lambda *a, **k: False)
    n = am.aplicar_migracoes(pool)
    assert n == len(_pendentes_a_partir_de("088"))
    assert _col_existe(pool, "funcionarios", "demitido_em")   # 094 aplicou
