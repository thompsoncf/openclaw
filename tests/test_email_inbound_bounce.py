"""finance/email_inbound.py::_tratar_bounce grava uma atividade 'bounce' — o
check de prospeccao_atividades nunca permitiu esse tipo, então o insert sempre
falhava e, por estar ANTES do commit da mesma transação, desfazia também o
email_ok=false e a parada da sequência (a própria proteção que o bounce deveria
dar). Migração 127 corrige o check; este teste prova que o fluxo completo
funciona e não regride.

Banco dedicado e descartável com o schema MÍNIMO que _tratar_bounce usa (mesmo
padrão do teste de custo de campanhas) — não replica migrações antigas.
"""
import os

import pytest
from psycopg_pool import ConnectionPool

from finance import email_inbound as ei

_BASE_SQL = """
create table contas (id bigserial primary key, tipo text, nome text, chip_de bigint);
create table prospeccao (id bigserial primary key, conta_id bigint, email text, email_ok boolean default true,
  atualizado_em timestamptz default now());
create table campanhas (id bigserial primary key, conta_id bigint);
create table campanha_alvos (id bigserial primary key, campanha_id bigint, prospeccao_id bigint,
  status text, proximo_envio_em timestamptz);
create table campanha_eventos (id bigserial primary key, campanha_id bigint, prospeccao_id bigint,
  canal text, evento text, detalhe text, quando timestamptz default now());
create table prospeccao_atividades (
  id bigserial primary key, prospeccao_id bigint, membro_id bigint,
  tipo text not null check (tipo in ('ligacao','whatsapp','email','reuniao','visita','nota','bounce')),
  descricao text not null default '', criado_em timestamptz not null default now());
"""


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_email_bounce_test"
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


@pytest.fixture()
def lead(pool):
    with pool.connection() as c:
        conta_id = c.execute(
            "insert into contas (tipo, nome) values ('pj','Thor Consultoria') returning id").fetchone()[0]
        prospeccao_id = c.execute(
            "insert into prospeccao (conta_id, email, email_ok) values (%s,%s,true) returning id",
            (conta_id, "rosamssr@hotmail.com")).fetchone()[0]
        campanha_id = c.execute(
            "insert into campanhas (conta_id) values (%s) returning id", (conta_id,)).fetchone()[0]
        c.execute(
            "insert into campanha_alvos (campanha_id, prospeccao_id, status) values (%s,%s,'enviado')",
            (campanha_id, prospeccao_id))
        c.commit()
    return {"conta_id": conta_id, "prospeccao_id": prospeccao_id, "campanha_id": campanha_id}


def test_bounce_marca_email_invalido_e_para_sequencia(pool, lead):
    m = {"assunto": "Undeliverable: Reunião", "corpo": "Falha na entrega pra rosamssr@hotmail.com"}
    with pool.connection() as c:
        n = ei._tratar_bounce(c, lead["conta_id"], m)
    assert n == 1

    with pool.connection() as c:
        email_ok = c.execute("select email_ok from prospeccao where id=%s",
                             (lead["prospeccao_id"],)).fetchone()[0]
        status = c.execute("select status from campanha_alvos where prospeccao_id=%s",
                           (lead["prospeccao_id"],)).fetchone()[0]
        ativ = c.execute("select tipo, descricao from prospeccao_atividades where prospeccao_id=%s",
                         (lead["prospeccao_id"],)).fetchone()
    assert email_ok is False                 # não regrediu por causa do insert que falhava
    assert status == "erro"                   # sequência parada
    assert ativ is not None and ativ[0] == "bounce"


def test_bounce_sem_email_reconhecido_nao_faz_nada(pool, lead):
    m = {"assunto": "Undeliverable", "corpo": "sem endereço nenhum aqui"}
    with pool.connection() as c:
        n = ei._tratar_bounce(c, lead["conta_id"], m)
    assert n == 0
