"""Regressão do módulo Vendas de Serviços (finance/vendas.fechar_orcamento).

Fechar um orçamento vira contrato e cai no financeiro (módulo Empresa) como
títulos a receber: setup (único) + mensalidade (recorrente, que se auto-renova
na baixa). É a ponte pipeline → receita, sem PDV novo pra serviço.

Roda com banco de TESTE separado (ver tests/conftest.py).
"""
import os
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from finance import vendas


@pytest.fixture(scope="module")
def pool():
    p = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=4,
                       open=True, kwargs={"prepare_threshold": None})
    init_schema(p)  # contas, membros, lancamentos
    base = Path(__file__).resolve().parent.parent / "db" / "migracoes"
    with p.connection() as c:
        c.execute((base / "053_modulo_pj.sql").read_text(encoding="utf-8"))  # titulos
        # tabela orcamentos (base do admin_orcamento) + colunas do pipeline
        c.execute("""create table if not exists orcamentos (
            id bigserial primary key, cliente text, empresa text, segmento text,
            setup_centavos bigint default 0, mensal_centavos bigint default 0,
            primeiro_ano_centavos bigint default 0, n_modulos int default 0,
            criado_em timestamptz default now())""")
        c.execute((base / "068_orcamento_pipeline.sql").read_text(encoding="utf-8"))
        c.execute((base / "069_orcamento_cnpj.sql").read_text(encoding="utf-8"))
        c.execute((base / "070_orcamento_conta.sql").read_text(encoding="utf-8"))
        c.commit()
    yield p
    p.close()


@pytest.fixture()
def conta_id(pool):
    with pool.connection() as c:
        cid = c.execute(
            "insert into contas (tipo, nome) values ('pj', 'Aladdin') returning id"
        ).fetchone()[0]
        c.commit()
    return cid


def _orc(pool, conta_id, *, empresa="Clínica X", setup=450000, mensal=210000,
         status="enviado"):
    with pool.connection() as c:
        oid = c.execute(
            """insert into orcamentos (conta_id, empresa, cliente, setup_centavos,
                   mensal_centavos, status) values (%s,%s,%s,%s,%s,%s) returning id""",
            (conta_id, empresa, "Dra. Ana", setup, mensal, status),
        ).fetchone()[0]
        c.commit()
    return oid


def _titulos(pool, conta_id):
    with pool.connection() as c:
        return c.execute(
            """select tipo, valor_centavos, recorrente, categoria, contraparte
                 from titulos where conta_id=%s order by id""", (conta_id,)
        ).fetchall()


def test_fechar_gera_setup_e_mensal_recorrente(pool, conta_id):
    oid = _orc(pool, conta_id, empresa="Clínica Sorriso", setup=450000, mensal=210000)
    r = vendas.fechar_orcamento(pool, conta_id, oid)
    assert r["ok"] and r["setup_titulo_id"] and r["mensal_titulo_id"]

    ts = _titulos(pool, conta_id)
    assert len(ts) == 2
    por_valor = {t[1]: t for t in ts}
    assert por_valor[450000][2] is False   # setup: não recorrente
    assert por_valor[210000][2] is True    # mensal: recorrente
    assert all(t[0] == "receber" and t[3] == "Serviços" for t in ts)
    assert all("Clínica Sorriso" in (t[4] or "") for t in ts)

    with pool.connection() as c:
        st = c.execute("select status from orcamentos where id=%s", (oid,)).fetchone()[0]
    assert st == "fechado"


def test_fechar_e_idempotente(pool, conta_id):
    oid = _orc(pool, conta_id, setup=100000, mensal=50000)
    assert vendas.fechar_orcamento(pool, conta_id, oid)["ok"] is True

    r2 = vendas.fechar_orcamento(pool, conta_id, oid)
    assert r2["ok"] is False and "fechado" in r2["erro"].lower()
    assert len(_titulos(pool, conta_id)) == 2   # não duplicou os títulos


def test_fechar_sem_setup_so_mensal(pool, conta_id):
    oid = _orc(pool, conta_id, setup=0, mensal=90000)
    r = vendas.fechar_orcamento(pool, conta_id, oid)
    assert r["ok"] and r["setup_titulo_id"] is None and r["mensal_titulo_id"]
    ts = _titulos(pool, conta_id)
    assert len(ts) == 1 and ts[0][2] is True


def test_fechar_orcamento_inexistente(pool, conta_id):
    r = vendas.fechar_orcamento(pool, conta_id, 999999)
    assert r["ok"] is False and "não encontrado" in r["erro"].lower()


def test_fechar_escopo_por_conta(pool, conta_id):
    """Uma empresa não pode fechar o orçamento de outra (multi-tenant)."""
    with pool.connection() as c:
        outra = c.execute(
            "insert into contas (tipo, nome) values ('pj', 'Outra') returning id"
        ).fetchone()[0]
        c.commit()
    oid = _orc(pool, outra, setup=100000, mensal=50000)      # orçamento é de OUTRA conta
    r = vendas.fechar_orcamento(pool, conta_id, oid)          # tenta com a conta errada
    assert r["ok"] is False and "não encontrado" in r["erro"].lower()
