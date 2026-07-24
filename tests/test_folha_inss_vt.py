"""Folha: desconto de INSS (progressivo por faixa) e vale-transporte (6%).

O líquido do funcionário passa a descontar INSS e, pra quem opta, vale-transporte.
Pró-labore (sócio) não usa a tabela CLT de INSS.
"""
import os
from datetime import date
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from finance import empresa as emp

_MIGRACOES = ("053_modulo_pj.sql", "089_funcionario_vale_transporte.sql",
              "092_funcionario_cbo.sql")


# ── parte pura: INSS progressivo e VT (sem banco) ────────────────────────────
@pytest.mark.parametrize("salario_reais,inss_reais", [
    (1000.00, 75.00),      # só 1a faixa: 7,5%
    (1621.00, 121.58),     # topo da 1a faixa (121,575 -> 121,58)
    (2000.00, 155.68),     # 1621*7,5% + (2000-1621)*9%
    (3000.00, 248.60),     # exemplo do enunciado (~248,61)
    (5000.00, 501.51),     # entra na 4a faixa
    (8475.55, 988.09),     # teto: desconto máximo
    (20000.00, 988.09),    # acima do teto trava no máximo
])
def test_inss_progressivo(salario_reais, inss_reais):
    got = emp.inss_desconto_centavos(round(salario_reais * 100))
    assert got == round(inss_reais * 100), (salario_reais, got / 100)


def test_inss_zero_e_negativo():
    assert emp.inss_desconto_centavos(0) == 0
    assert emp.inss_desconto_centavos(-500) == 0


def test_vale_transporte_6pct():
    assert emp.vale_transporte_desconto_centavos(300000) == 18000      # 6% de 3000
    assert emp.vale_transporte_desconto_centavos(300000, True) == 18000
    assert emp.vale_transporte_desconto_centavos(300000, False) == 0   # não optou


# ── parte com banco: folha_do_mes desconta INSS + VT do líquido ──────────────
@pytest.fixture(scope="module")
def pool():
    test_db_url = os.environ["TEST_DATABASE_URL"]
    p = ConnectionPool(test_db_url, min_size=1, max_size=4, open=True,
                       kwargs={"prepare_threshold": None})
    init_schema(p)
    base = Path(__file__).resolve().parent.parent / "db" / "migracoes"
    for m in _MIGRACOES:
        with p.connection() as c:
            c.execute((base / m).read_text(encoding="utf-8"))
            c.commit()
    yield p
    p.close()


@pytest.fixture()
def conta_id(pool):
    with pool.connection() as c:
        cid = c.execute(
            "insert into contas (tipo, nome) values ('pj', 'Teste Folha') returning id"
        ).fetchone()[0]
        c.commit()
    return cid


def _item(folha, fid):
    return next(i for i in folha["itens"] if i["id"] == fid)


def test_folha_desconta_inss(pool, conta_id):
    f = emp.criar_funcionario(pool, conta_id, "João", salario_centavos=300000)
    hoje = date.today()
    folha = emp.folha_do_mes(pool, conta_id, hoje.year, hoje.month)
    it = _item(folha, f["id"])
    assert it["inss_centavos"] == 24860       # R$248,60
    assert it["vt_centavos"] == 0             # não optou por VT
    # líquido = 3000 - INSS = 2751,40
    assert it["liquido_centavos"] == 300000 - 24860
    assert it["a_pagar_centavos"] == 300000 - 24860


def test_folha_desconta_inss_e_vt(pool, conta_id):
    f = emp.criar_funcionario(pool, conta_id, "Maria", salario_centavos=300000,
                              vale_transporte=True)
    hoje = date.today()
    folha = emp.folha_do_mes(pool, conta_id, hoje.year, hoje.month)
    it = _item(folha, f["id"])
    assert it["inss_centavos"] == 24860
    assert it["vt_centavos"] == 18000         # 6% de 3000
    # líquido = 3000 - 248,60 - 180 = 2571,40
    assert it["liquido_centavos"] == 300000 - 24860 - 18000
    assert it["vale_transporte"] is True


def test_inss_incide_sobre_salario_mais_extras(pool, conta_id):
    f = emp.criar_funcionario(pool, conta_id, "Carla", salario_centavos=200000)
    hoje = date.today()
    # hora-extra de R$500 na competência -> base do INSS = 2500
    emp.registrar_evento_folha(pool, conta_id, f["id"], "extra", 50000,
                               competencia=hoje)
    folha = emp.folha_do_mes(pool, conta_id, hoje.year, hoje.month)
    it = _item(folha, f["id"])
    assert it["extras_centavos"] == 50000
    # INSS sobre 2500 (salário + extra), não sobre 2000
    assert it["inss_centavos"] == emp.inss_desconto_centavos(250000)
    assert it["inss_centavos"] != emp.inss_desconto_centavos(200000)
    # líquido = 2000 + 500 - INSS(2500)
    assert it["liquido_centavos"] == 250000 - emp.inss_desconto_centavos(250000)


def test_prolabore_nao_tem_inss_clt(pool, conta_id):
    f = emp.criar_funcionario(pool, conta_id, "Sócio", salario_centavos=300000,
                              pro_labore=True)
    hoje = date.today()
    folha = emp.folha_do_mes(pool, conta_id, hoje.year, hoje.month)
    it = _item(folha, f["id"])
    assert it["inss_centavos"] == 0           # não usa tabela CLT
    assert it["liquido_centavos"] == 300000


def test_toggle_vale_transporte(pool, conta_id):
    f = emp.criar_funcionario(pool, conta_id, "Pedro", salario_centavos=200000)
    assert emp.atualizar_funcionario(pool, conta_id, f["id"], vale_transporte=True)
    funcs = {x["id"]: x for x in emp.listar_funcionarios(pool, conta_id)}
    assert funcs[f["id"]]["vale_transporte"] is True
