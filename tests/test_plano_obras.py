"""1.1.04 Venda de Imóveis e 3.1.04 Mão de Obra de Obras (migração 349): as duas
contas entram no grupo certo da DRE, no lugar certo da árvore, e ligadas pra
quem não desligou.

Banco PRÓPRIO, pelo mesmo motivo do test_plano_fardamentos: a suíte compartilha
o banco de teste, e `test_plano_contas` conta as contas da 132+143.
"""
import os
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from finance import plano_contas as pc

_MIGRACOES = ("018_chave_nfce_lancamentos.sql", "053_modulo_pj.sql",
              "057_natureza_lancamento.sql", "132_plano_contas_centros_custo.sql",
              "143_plano_contas_locacao_buffet_servicos.sql", "186_plano_aporte_socios.sql",
              "336_plano_fardamentos.sql", "349_plano_obras.sql")
_BASE = Path(__file__).resolve().parent.parent / "db" / "migracoes"


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_plano_obras_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname} with (force)")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    init_schema(p)
    for m in _MIGRACOES:
        with p.connection() as c:
            c.execute((_BASE / m).read_text(encoding="utf-8"))
            c.commit()
    yield p
    p.close()


def test_venda_de_imoveis_e_receita_operacional(pool):
    c = {x["codigo"]: x for x in pc.listar_plano(pool)}["1.1.04"]
    assert (c["nome"], c["grupo"], c["natureza"]) == ("Venda de Imóveis", 1, "receita")


def test_mao_de_obra_de_obras_e_custo_ao_lado_do_material(pool):
    """Custo da casa, no grupo 3 com Insumos e Materiais (3.1.03) — não despesa
    operacional, onde Serviços Terceirizados (5.1.10) a deixaria."""
    por_cod = {x["codigo"]: x for x in pc.listar_plano(pool)}
    c = por_cod["3.1.04"]
    assert (c["nome"], c["grupo"], c["natureza"]) == ("Mão de Obra de Obras", 3, "despesa")
    assert por_cod["3.1.03"]["grupo"] == 3


def test_as_duas_caem_no_lugar_e_a_ordem_segue_o_codigo(pool):
    plano = pc.listar_plano(pool)
    codigos = [c["codigo"] for c in plano]
    assert codigos == sorted(codigos)
    assert [c["ordem"] for c in plano] == list(range(1, len(plano) + 1))
    i = codigos.index("1.1.04")
    assert codigos[i - 1] == "1.1.03" and codigos[i + 1].startswith("1.2.")
    j = codigos.index("3.1.04")
    assert codigos[j - 1] == "3.1.03"


def test_rerodar_nao_duplica(pool):
    antes = len(pc.listar_plano(pool))
    with pool.connection() as c:
        c.execute((_BASE / "349_plano_obras.sql").read_text(encoding="utf-8"))
        c.commit()
    plano = pc.listar_plano(pool)
    assert len(plano) == antes
    for cod in ("1.1.04", "3.1.04"):
        assert sum(1 for c in plano if c["codigo"] == cod) == 1


def test_entram_ligadas_e_aparecem_no_lancamento(pool):
    with pool.connection() as c:
        cid = c.execute("insert into contas (tipo, nome) values ('pj', 'PX2') returning id").fetchone()[0]
        c.commit()
    grupos = {g["grupo"]: [ct["nome"] for ct in g["contas"]]
              for g in pc.opcoes_lancamento(pool, cid)}
    assert "Venda de Imóveis" in grupos[1]
    assert "Mão de Obra de Obras" in grupos[3]
    assert pc.id_por_codigo(pool, cid, "1.1.04") and pc.id_por_codigo(pool, cid, "3.1.04")
