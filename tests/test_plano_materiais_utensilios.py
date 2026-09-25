"""5.1.12 Materiais e Utensílios (migração 351): entra em Despesas Operacionais,
no lugar certo da árvore, e ligada pra quem não desligou.

Banco PRÓPRIO, pelo mesmo motivo do `test_plano_fardamentos`: a suíte compartilha
o banco de teste e `test_plano_contas` conta as 37 contas da 132+143 — aplicar a
351 lá mudaria a conta dele.

A armadilha específica desta conta é o CÓDIGO. Ela é a primeira com dois dígitos
depois da 5.1.11, e a `ordem` do plano é recalculada por `order by codigo`, que é
ordenação de TEXTO. Se um dia alguém acrescentar uma 5.1.9 (um dígito), ela cairia
depois da 5.1.12 e a árvore sairia fora de ordem na tela. O teste da ordem abaixo
é o que trava isso.
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
              "336_plano_fardamentos.sql", "351_plano_materiais_utensilios.sql")
_BASE = Path(__file__).resolve().parent.parent / "db" / "migracoes"


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_plano_utensilios_test"
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


def test_fica_em_despesas_operacionais(pool):
    por_cod = {c["codigo"]: c for c in pc.listar_plano(pool)}
    u = por_cod["5.1.12"]
    assert (u["nome"], u["grupo"], u["natureza"]) == ("Materiais e Utensílios", 5, "despesa")


def test_o_grupo_5_e_despesa_operacional_e_nao_custo(pool):
    """O vizinho perigoso é 3.1.03 Insumos e Materiais, que é CUSTO. Se a 5.1.12
    cair no grupo 3, embalagem e utensílio viram a mesma coisa e a margem por venda
    passa a mentir."""
    assert pc.GRUPOS_DRE[5]["nome"] == "Despesas Operacionais"
    por_cod = {c["codigo"]: c for c in pc.listar_plano(pool)}
    assert por_cod["3.1.03"]["grupo"] == 3, "Insumos continua sendo custo"
    assert por_cod["5.1.12"]["grupo"] != por_cod["3.1.03"]["grupo"]


def test_cai_logo_depois_da_5_1_11_e_a_ordem_segue_o_codigo(pool):
    plano = pc.listar_plano(pool)
    codigos = [c["codigo"] for c in plano]
    assert codigos == sorted(codigos)
    assert [c["ordem"] for c in plano] == list(range(1, len(plano) + 1))
    i = codigos.index("5.1.12")
    assert codigos[i - 1] == "5.1.11"
    assert codigos[i + 1].startswith("6."), "a 5.1.12 fecha o grupo 5"


def test_rerodar_nao_duplica(pool):
    antes = len(pc.listar_plano(pool))
    with pool.connection() as c:
        c.execute((_BASE / "351_plano_materiais_utensilios.sql").read_text(encoding="utf-8"))
        c.commit()
    plano = pc.listar_plano(pool)
    assert len(plano) == antes and sum(1 for c in plano if c["codigo"] == "5.1.12") == 1


def test_entra_ligada_e_aparece_no_lancamento(pool):
    with pool.connection() as c:
        cid = c.execute("insert into contas (tipo, nome) values ('pj', 'Prime') returning id").fetchone()[0]
        c.commit()
    g5 = next(g for g in pc.opcoes_lancamento(pool, cid) if g["grupo"] == 5)
    assert "Materiais e Utensílios" in [ct["nome"] for ct in g5["contas"]]
    assert pc.id_por_codigo(pool, cid, "5.1.12")


def test_quem_desligar_deixa_de_ver(pool):
    """O único jeito de uma conta global não servir pra uma empresa: ela desliga.
    É o que o aviso manda fazer, então tem que funcionar."""
    with pool.connection() as c:
        cid = c.execute("insert into contas (tipo, nome) values ('pj', 'Consultoria') "
                        "returning id").fetchone()[0]
        c.commit()
    pid = pc.id_por_codigo(pool, cid, "5.1.12")
    pc.habilitar(pool, cid, pid, False)
    g5 = next(g for g in pc.opcoes_lancamento(pool, cid) if g["grupo"] == 5)
    assert "Materiais e Utensílios" not in [ct["nome"] for ct in g5["contas"]]
    # e não vaza pra outra empresa: multi-tenant
    with pool.connection() as c:
        outra = c.execute("insert into contas (tipo, nome) values ('pj', 'Buffet') "
                          "returning id").fetchone()[0]
        c.commit()
    g5o = next(g for g in pc.opcoes_lancamento(pool, outra) if g["grupo"] == 5)
    assert "Materiais e Utensílios" in [ct["nome"] for ct in g5o["contas"]]
