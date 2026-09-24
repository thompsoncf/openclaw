"""4.1.06 Fardamentos (migração 336): a conta entra em Despesas com Pessoal, no
lugar certo da árvore, e ligada pra quem não desligou.

Banco PRÓPRIO: a suíte compartilha o banco de teste, e `test_plano_contas` conta
as 37 contas da 132+143 — aplicar a 336 lá mudaria a conta dele.
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
              "336_plano_fardamentos.sql")
_BASE = Path(__file__).resolve().parent.parent / "db" / "migracoes"


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_plano_fardamentos_test"
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


def test_fardamentos_fica_em_despesas_com_pessoal(pool):
    por_cod = {c["codigo"]: c for c in pc.listar_plano(pool)}
    f = por_cod["4.1.06"]
    assert (f["nome"], f["grupo"], f["natureza"]) == ("Fardamentos", 4, "despesa")


def test_cai_logo_depois_da_4_1_05_e_a_ordem_segue_o_codigo(pool):
    plano = pc.listar_plano(pool)
    codigos = [c["codigo"] for c in plano]
    assert codigos == sorted(codigos)
    assert [c["ordem"] for c in plano] == list(range(1, len(plano) + 1))
    i = codigos.index("4.1.06")
    assert codigos[i - 1] == "4.1.05" and codigos[i + 1].startswith("5.")


def test_rerodar_nao_duplica(pool):
    antes = len(pc.listar_plano(pool))
    with pool.connection() as c:
        c.execute((_BASE / "336_plano_fardamentos.sql").read_text(encoding="utf-8"))
        c.commit()
    plano = pc.listar_plano(pool)
    assert len(plano) == antes and sum(1 for c in plano if c["codigo"] == "4.1.06") == 1


def test_entra_ligada_e_aparece_no_lancamento(pool):
    with pool.connection() as c:
        cid = c.execute("insert into contas (tipo, nome) values ('pj', 'Prime') returning id").fetchone()[0]
        c.commit()
    g4 = next(g for g in pc.opcoes_lancamento(pool, cid) if g["grupo"] == 4)
    assert "Fardamentos" in [ct["nome"] for ct in g4["contas"]]
    assert pc.id_por_codigo(pool, cid, "4.1.06")
