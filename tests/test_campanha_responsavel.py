"""Vínculo de equipe na campanha (responsavel_id, espelha vendedor_id do lead):

- _campanhas_dados(c, conta, membro_id) restringe às campanhas em que o membro é o
  responsável (visão do vendedor); sem membro_id vê todas (dono/gestor).
- _pode_campanha(ctx, camp_id, c) libera dono/gestor em tudo e o vendedor só na
  campanha vinculada a ele.

Banco dedicado e descartável com o schema mínimo (mesmo padrão do teste de blindagem).
"""
import os

import pytest
from psycopg_pool import ConnectionPool

from web import painel_prospeccao as pp

_BASE_SQL = """
create table contas (id bigserial primary key, tipo text, nome text);
create table membros (id bigserial primary key, conta_id bigint, nome text, email text);
create table prospeccao (id bigserial primary key, conta_id bigint, estagio text,
  vendedor_id bigint);   -- dono do lead: escopo do KPI de conversa no chat
create table campanhas (id bigserial primary key, conta_id bigint, nome text, status text,
  limite_dia int default 40, wa_ativo boolean default false, wa_template_sid text,
  enviados_hoje int default 0, dia_contagem date, teto_wa numeric(10,2),
  responsavel_id bigint, wa_bloqueio text,
  criado_em timestamptz default now());
create table campanha_alvos (id bigserial primary key, campanha_id bigint, prospeccao_id bigint,
  status text, aberturas int, wa_status text, wa_custo numeric(10,4), proximo_envio_em timestamptz);
create table campanha_eventos (id bigserial primary key, campanha_id bigint, prospeccao_id bigint,
  canal text, evento text, detalhe text, quando timestamptz default now());
-- conversas/mensagens: os KPIs de "Gastos das campanhas" passaram a contar quem
-- ESCREVEU no chat (e quantos desses nunca receberam resposta humana), sinal que
-- os contadores de botão não pegam. Sem as tabelas aqui, _campanhas_dados quebra.
create table conversas (id bigserial primary key, conta_id bigint, prospeccao_id bigint);
create table mensagens (id bigserial primary key, conversa_id bigint, direcao text,
  autor text, texto text, criado_em timestamptz default now());
create table canais_config (
  id bigserial primary key, conta_id bigint, canal text, identificador text,
  ativo boolean not null default true, token text, provedor text not null default 'twilio',
  wa_phone_id text);
create table app_config (chave text primary key, valor text not null,
  atualizado_em timestamptz not null default now());
"""


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_campanha_responsavel_test"
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


def _setup(c):
    conta = c.execute("insert into contas (tipo, nome) values ('pj','Equipe') returning id").fetchone()[0]
    ana = c.execute("insert into membros (conta_id, nome) values (%s,'Ana') returning id", (conta,)).fetchone()[0]
    beto = c.execute("insert into membros (conta_id, nome) values (%s,'Beto') returning id", (conta,)).fetchone()[0]
    c_ana = c.execute("insert into campanhas (conta_id, nome, status, responsavel_id) values (%s,'Da Ana','ativa',%s) returning id",
                      (conta, ana)).fetchone()[0]
    c_beto = c.execute("insert into campanhas (conta_id, nome, status, responsavel_id) values (%s,'Do Beto','ativa',%s) returning id",
                       (conta, beto)).fetchone()[0]
    c_livre = c.execute("insert into campanhas (conta_id, nome, status) values (%s,'Sem dono','ativa') returning id",
                        (conta,)).fetchone()[0]
    c.commit()
    return conta, ana, beto, c_ana, c_beto, c_livre


def test_dados_filtra_por_responsavel(pool):
    with pool.connection() as c:
        conta, ana, beto, c_ana, c_beto, c_livre = _setup(c)
        # dono (membro_id=None) vê as 3
        todas, _ = pp._campanhas_dados(c, conta)
        # Ana (membro_id=ana) vê só a dela
        da_ana, _ = pp._campanhas_dados(c, conta, ana)
    assert {x["id"] for x in todas} == {c_ana, c_beto, c_livre}
    ids_ana = {x["id"] for x in da_ana}
    assert ids_ana == {c_ana}
    assert next(x for x in todas if x["id"] == c_ana)["responsavel"] == "Ana"
    assert next(x for x in todas if x["id"] == c_livre)["responsavel"] == ""


def test_pode_campanha_gerencia_e_responsavel(pool):
    with pool.connection() as c:
        conta, ana, beto, c_ana, c_beto, c_livre = _setup(c)
        dono = {"conta_id": conta, "membro_id": None, "gerencia": True}
        ctx_ana = {"conta_id": conta, "membro_id": ana, "gerencia": False}
        # dono/gestor pode em qualquer uma da conta
        assert pp._pode_campanha(dono, c_livre, c) is True
        assert pp._pode_campanha(dono, c_beto, c) is True
        # Ana só na dela
        assert pp._pode_campanha(ctx_ana, c_ana, c) is True
        assert pp._pode_campanha(ctx_ana, c_beto, c) is False
        assert pp._pode_campanha(ctx_ana, c_livre, c) is False
        # campanha de outra conta nunca
        outra = c.execute("insert into contas (tipo, nome) values ('pj','Outra') returning id").fetchone()[0]
        cx = c.execute("insert into campanhas (conta_id, nome, status, responsavel_id) values (%s,'X','ativa',%s) returning id",
                       (outra, ana)).fetchone()[0]
        c.commit()
        assert pp._pode_campanha(ctx_ana, cx, c) is False
        assert pp._pode_campanha(dono, cx, c) is False
