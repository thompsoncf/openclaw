"""Regressão da proposta pública (web.proposta): carregar + assinar.

O cliente abre /proposta/<token>, vê a proposta e assina (nome + aceite). Assinar
marca 'aprovada' (idempotente) e registra nome/data/IP. Aqui testamos a camada de
dados (sem HTTP), com banco de TESTE separado (ver tests/conftest.py).
"""
import json
import os

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from web import proposta as prop
from web.painel_servicos import _garantir_tabela


@pytest.fixture(scope="module")
def pool():
    p = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=4,
                       open=True, kwargs={"prepare_threshold": None})
    init_schema(p)
    base = os.path.join(os.path.dirname(__file__), "..", "db", "migracoes")
    with p.connection() as c:
        c.execute(open(os.path.join(base, "053_modulo_pj.sql"), encoding="utf-8").read())
        c.execute("""create table if not exists orcamentos (
            id bigserial primary key, cliente text, empresa text, segmento text,
            setup_centavos bigint default 0, mensal_centavos bigint default 0,
            primeiro_ano_centavos bigint default 0, n_modulos int default 0,
            criado_em timestamptz default now())""")
        _garantir_tabela(c)   # colunas do pipeline + token/itens/aprovada_*
        c.commit()
    yield p
    p.close()


def _semear(pool, *, status="enviado", token="TK1"):
    itens = json.dumps([{"nome": "Agente", "desc": "24/7", "setup": 4500, "mensal": 1200}])
    with pool.connection() as c:
        cid = c.execute(
            "insert into contas (tipo, nome) values ('pj','Aladdin Tech') returning id").fetchone()[0]
        c.execute(
            """insert into orcamentos (conta_id, empresa, cliente, escopo, itens,
                   setup_centavos, mensal_centavos, primeiro_ano_centavos, status, token)
               values (%s,'Clínica Sorriso','Dra. Ana','Escopo.',%s::jsonb,
                       920000,210000,3440000,%s,%s)""",
            (cid, itens, status, token))
        c.commit()
    return token


def test_carregar_traz_marca_e_itens(pool):
    _semear(pool, token="TKA")
    d = prop._carregar("TKA", pool=pool)
    assert d and d["vendedor"] == "Aladdin Tech"        # marca = empresa que vendeu
    assert d["empresa"] == "Clínica Sorriso"
    assert d["itens"] and d["itens"][0]["nome"] == "Agente"
    assert d["setup"] == "R$ 9.200" and d["ano1"] == "R$ 34.400"


def test_carregar_token_inexistente(pool):
    assert prop._carregar("naoexiste", pool=pool) is None


def test_assinar_marca_aprovada_e_idempotente(pool):
    _semear(pool, token="TKB")
    assert prop.registrar_assinatura(pool, "TKB", "Ana Paula", "", "1.2.3.4") is True
    d = prop._carregar("TKB", pool=pool)
    assert d["status"] == "aprovada" and d["aprovada_por"] == "Ana Paula"
    # assinar de novo não muda nada (idempotente)
    assert prop.registrar_assinatura(pool, "TKB", "Outro Nome", "", "9.9.9.9") is False
    d2 = prop._carregar("TKB", pool=pool)
    assert d2["aprovada_por"] == "Ana Paula"            # manteve o primeiro


def test_assinar_sem_nome_recusa(pool):
    _semear(pool, token="TKC")
    assert prop.registrar_assinatura(pool, "TKC", "   ", "", "1.2.3.4") is False
    assert prop._carregar("TKC", pool=pool)["status"] == "enviado"


def test_assinar_nao_mexe_em_fechada(pool):
    _semear(pool, status="fechado", token="TKD")
    assert prop.registrar_assinatura(pool, "TKD", "Ana", "", "1.2.3.4") is False
    assert prop._carregar("TKD", pool=pool)["status"] == "fechado"
