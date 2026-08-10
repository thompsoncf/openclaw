"""Identidade & Marca — o molde de logo/marca do fornecedor levado a toda empresa PJ.
Cobre os helpers de empresa.py (obter/salvar identidade + kit de marca).

Roda com banco de TESTE separado (ver tests/conftest.py)."""
import os
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from finance import empresa as emp

_COLS_IDENTIDADE = """
alter table contas add column if not exists logo_url text;
alter table contas add column if not exists banner_url text;
alter table contas add column if not exists banner_cor text;
alter table contas add column if not exists bio text;
alter table contas add column if not exists sobre text;
alter table contas add column if not exists whatsapp_loja text;
alter table contas add column if not exists nome_fantasia text;
"""


@pytest.fixture(scope="module")
def pool():
    url = os.environ["TEST_DATABASE_URL"]  # garantido pela trava do conftest
    p = ConnectionPool(url, min_size=1, max_size=4, open=True,
                       kwargs={"prepare_threshold": None})
    init_schema(p)  # idempotente
    with p.connection() as c:
        c.execute(_COLS_IDENTIDADE)  # colunas de marca (migrações 044/045/058)
        c.commit()
    yield p
    p.close()


@pytest.fixture()
def conta_id(pool):
    with pool.connection() as c:
        cid = c.execute(
            "insert into contas (tipo, nome) values ('pj', 'Teste Marca') returning id"
        ).fetchone()[0]
        c.commit()
    return cid


def _set(pool, conta_id, **col):
    campos = ", ".join(f"{k}=%s" for k in col)
    with pool.connection() as c:
        c.execute(f"update contas set {campos} where id=%s",
                  (*col.values(), conta_id))
        c.commit()


def test_salvar_e_obter_identidade(pool, conta_id):
    emp.salvar_identidade(pool, conta_id, bio="Do produtor à mesa",
                          sobre="Desde 1998 abastecendo a cidade.",
                          whatsapp_loja="(86) 99999-0000", banner_cor="#2f7d32")
    d = emp.obter_identidade(pool, conta_id)
    assert d["bio"] == "Do produtor à mesa"
    assert d["sobre"].startswith("Desde 1998")
    assert d["whatsapp_loja"] == "(86) 99999-0000"
    assert d["banner_cor"] == "#2f7d32"


def test_cor_invalida_vira_none_e_hex_sem_hash_aceita(pool, conta_id):
    emp.salvar_identidade(pool, conta_id, banner_cor="verde-limão")
    assert emp.obter_identidade(pool, conta_id)["banner_cor"] == ""   # inválida -> NULL
    emp.salvar_identidade(pool, conta_id, banner_cor="c8722f")        # sem '#'
    assert emp.obter_identidade(pool, conta_id)["banner_cor"] == "#c8722f"


def test_salvar_vazio_limpa(pool, conta_id):
    emp.salvar_identidade(pool, conta_id, bio="X", sobre="Y")
    emp.salvar_identidade(pool, conta_id)  # tudo vazio
    d = emp.obter_identidade(pool, conta_id)
    assert d["bio"] == "" and d["sobre"] == "" and d["whatsapp_loja"] == ""


def test_marca_empresa_usa_logo_quando_existe(pool, conta_id):
    _set(pool, conta_id, nome_fantasia="Grupo Manoel Soares",
         logo_url="https://x/logo.jpg", banner_cor="#123abc")
    m = emp.marca_empresa(pool, conta_id)
    assert m["logo_url"] == "https://x/logo.jpg"
    assert m["cor"] == "#123abc"
    assert m["iniciais"] == "GM"
    assert m["nome"] == "Grupo Manoel Soares"


def test_marca_empresa_fallback_iniciais_e_cor_padrao(pool, conta_id):
    _set(pool, conta_id, nome_fantasia="Padaria", logo_url=None, banner_cor=None)
    m = emp.marca_empresa(pool, conta_id)
    assert m["logo_url"] is None
    assert m["iniciais"] == "P"        # uma palavra -> 1 inicial
    assert m["cor"] == "#2f7d32"       # cor padrão quando não há
