"""Distribuição de leads por rodízio (finance/distribuicao):

- proximo_vendedor gira em círculo e avança o ponteiro; pula membro inativo;
- atribuir_se_sem_dono atribui só quando o lead não tem dono (nunca rouba);
- salvar grava a fila na ordem e zera o ponteiro; desligado não distribui.

Banco dedicado e descartável com o schema mínimo (mesmo padrão do teste de blindagem).
"""
import os

import pytest
from psycopg_pool import ConnectionPool

from finance import distribuicao as dist

_BASE_SQL = """
create table contas (id bigserial primary key, nome text);
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  ativo boolean default true, whatsapp text);
create table prospeccao (id bigserial primary key, conta_id bigint, empresa text,
  vendedor_id bigint, atualizado_em timestamptz default now());
create table conversas (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  responsavel_membro_id bigint);
create table distribuicao (conta_id bigint primary key, ativo boolean not null default false,
  ponteiro int not null default 0, avisar boolean not null default true,
  atualizado_em timestamptz not null default now());
create table distribuicao_fila (conta_id bigint, membro_id bigint, ordem int not null default 0,
  primary key (conta_id, membro_id));
"""


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_distribuicao_test"
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


def _setup(c, nome_conta, n=3, ativo=True):
    conta = c.execute("insert into contas (nome) values (%s) returning id", (nome_conta,)).fetchone()[0]
    ids = []
    for i in range(n):
        ids.append(c.execute("insert into membros (conta_id, nome, email) values (%s,%s,%s) returning id",
                             (conta, f"V{i+1}", f"v{i+1}-{conta}@x.com")).fetchone()[0])
    dist.salvar(c, conta, ativo, True, ids)
    c.commit()
    return conta, ids


def _lead(c, conta):
    return c.execute("insert into prospeccao (conta_id, empresa) values (%s,'Lead') returning id",
                     (conta,)).fetchone()[0]


def test_rodizio_circular_e_ponteiro(pool):
    with pool.connection() as c:
        conta, ids = _setup(c, "Rod", 3)
        seq = [dist.proximo_vendedor(c, conta) for _ in range(7)]
        c.commit()
    assert seq == [ids[0], ids[1], ids[2], ids[0], ids[1], ids[2], ids[0]]   # gira em círculo


def test_pula_inativo(pool):
    with pool.connection() as c:
        conta, ids = _setup(c, "Inativo", 3)
        c.execute("update membros set ativo=false where id=%s", (ids[1],))   # V2 sai
        c.commit()
        seq = [dist.proximo_vendedor(c, conta) for _ in range(4)]
        c.commit()
    assert set(seq) == {ids[0], ids[2]}          # V2 nunca é escolhido
    assert ids[1] not in seq


def test_desligado_nao_distribui(pool):
    with pool.connection() as c:
        conta, ids = _setup(c, "Off", 3, ativo=False)
        assert dist.proximo_vendedor(c, conta) is None
        pid = _lead(c, conta)
        assert dist.atribuir_se_sem_dono(c, conta, pid) is None
        c.commit()
        assert c.execute("select vendedor_id from prospeccao where id=%s", (pid,)).fetchone()[0] is None


def test_atribui_lead_sem_dono_e_marca_conversa(pool):
    with pool.connection() as c:
        conta, ids = _setup(c, "Atrib", 2)
        pid = _lead(c, conta)
        conv = c.execute("insert into conversas (conta_id, prospeccao_id) values (%s,%s) returning id",
                         (conta, pid)).fetchone()[0]
        mid = dist.atribuir_se_sem_dono(c, conta, pid)
        c.commit()
    assert mid == ids[0]
    with pool.connection() as c:
        assert c.execute("select vendedor_id from prospeccao where id=%s", (pid,)).fetchone()[0] == ids[0]
        assert c.execute("select responsavel_membro_id from conversas where id=%s", (conv,)).fetchone()[0] == ids[0]


def test_nao_rouba_lead_com_dono(pool):
    with pool.connection() as c:
        conta, ids = _setup(c, "Rouba", 2)
        pid = c.execute("insert into prospeccao (conta_id, empresa, vendedor_id) values (%s,'X',%s) returning id",
                        (conta, ids[1])).fetchone()[0]
        mid = dist.atribuir_se_sem_dono(c, conta, pid)
        c.commit()
    assert mid is None
    with pool.connection() as c:
        assert c.execute("select vendedor_id from prospeccao where id=%s", (pid,)).fetchone()[0] == ids[1]


def test_membros_fila_ui_ordem(pool):
    with pool.connection() as c:
        conta, ids = _setup(c, "UI", 3)
        # só os 2 primeiros na fila, na ordem invertida
        dist.salvar(c, conta, True, True, [ids[1], ids[0]])
        c.commit()
        ui = dist.membros_fila_ui(c, conta)
    na_fila = [m["id"] for m in ui if m["na_fila"]]
    assert na_fila == [ids[1], ids[0]]           # respeita a ordem salva
    assert ids[2] in [m["id"] for m in ui if not m["na_fila"]]   # o de fora aparece depois
