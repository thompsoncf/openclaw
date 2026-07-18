"""Regressão do catálogo de serviços por conta (finance.servicos_catalogo).

Cada empresa tem o próprio catálogo (multi-tenant). Empresa nova começa vazia;
adiciona/edita/exclui o que vende. Exclusão é soft (não some do banco, some da
lista). Roda com banco de TESTE separado (ver tests/conftest.py).
"""
import os

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from finance import servicos_catalogo as cat


@pytest.fixture(scope="module")
def pool():
    p = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=4,
                       open=True, kwargs={"prepare_threshold": None})
    init_schema(p)              # contas
    cat.garantir_tabela(p)      # servicos_catalogo
    yield p
    p.close()


@pytest.fixture()
def conta_id(pool):
    with pool.connection() as c:
        cid = c.execute(
            "insert into contas (tipo, nome) values ('pj', 'Empresa X') returning id"
        ).fetchone()[0]
        c.commit()
    return cid


def test_empresa_nova_comeca_vazia(pool, conta_id):
    assert cat.listar(pool, conta_id) == []


def test_adiciona_edita_exclui(pool, conta_id):
    r = cat.salvar(pool, conta_id, nome="Consultoria de SEO",
                   descricao="Auditoria + otimização", setup_centavos=250000,
                   mensal_centavos=90000, custo_centavos=30000)
    assert r["ok"] and r["id"] and r["slug"] == "consultoria-de-seo"

    itens = cat.listar(pool, conta_id)
    assert len(itens) == 1
    it = itens[0]
    assert it["nome"] == "Consultoria de SEO" and it["setup_centavos"] == 250000

    # edita
    e = cat.salvar(pool, conta_id, id=it["id"], nome="Consultoria SEO+",
                   mensal_centavos=120000)
    assert e["ok"]
    it2 = cat.listar(pool, conta_id)[0]
    assert it2["nome"] == "Consultoria SEO+" and it2["mensal_centavos"] == 120000

    # exclui (soft): some da lista
    assert cat.excluir(pool, conta_id, it["id"])["ok"] is True
    assert cat.listar(pool, conta_id) == []


def test_nome_vazio_recusa(pool, conta_id):
    r = cat.salvar(pool, conta_id, nome="   ")
    assert r["ok"] is False


def test_slug_unico_por_conta(pool, conta_id):
    a = cat.salvar(pool, conta_id, nome="Site")
    b = cat.salvar(pool, conta_id, nome="Site")
    assert a["slug"] == "site" and b["slug"] == "site-2"


def test_escopo_por_conta(pool, conta_id):
    """Uma conta não enxerga nem edita o catálogo de outra."""
    with pool.connection() as c:
        outra = c.execute(
            "insert into contas (tipo, nome) values ('pj', 'Outra') returning id"
        ).fetchone()[0]
        c.commit()
    r = cat.salvar(pool, outra, nome="Serviço da outra")
    assert cat.listar(pool, conta_id) == []           # não vaza
    # não consegue editar/excluir o item da outra
    assert cat.excluir(pool, conta_id, r["id"])["ok"] is False


def test_importar_modelo_idempotente(pool, conta_id):
    r1 = cat.importar_modelo(pool, conta_id)
    assert r1["ok"] and r1["inseridos"] == len(cat.STARTER_TECNOLOGIA)
    itens = cat.listar(pool, conta_id)
    assert len(itens) == len(cat.STARTER_TECNOLOGIA)
    # slug preservado + valores convertidos pra centavos
    por_slug = {i["slug"]: i for i in itens}
    assert por_slug["crm"]["setup_centavos"] == 350000
    # reimportar não duplica
    r2 = cat.importar_modelo(pool, conta_id)
    assert r2["inseridos"] == 0
    assert len(cat.listar(pool, conta_id)) == len(cat.STARTER_TECNOLOGIA)
