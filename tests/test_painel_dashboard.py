"""Integração do dashboard do /painel (_painel_dashboard).

Semeia uma conta PJ com títulos (a pagar/receber/atrasado/recorrente),
lançamentos do mês (receita+despesa) e propostas em vários status, e verifica
que o helper monta os números do dashboard corretamente:
  • resumo de títulos (a receber/pagar, atraso);
  • MRR (receita recorrente aberta);
  • DRE do mês (receitas/despesas/resultado);
  • funil de propostas (total, conversão, valor previsto, legenda, donut);
  • série do fluxo projetado (poly/area p/ o SVG).

Roda isolado: a fixture aplica schema + migrações no TEST_DATABASE_URL (a trava
do conftest garante que nunca é o banco de produção).
"""
import os
from datetime import date, timedelta
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from finance import empresa as emp
from web import portal
from web.painel_servicos import _garantir_tabela as _garantir_orcamentos

# 018 = chave em lancamentos; 053 = titulos; 057 = natureza em lancamentos.
_MIGRACOES = ["018_chave_nfce_lancamentos.sql", "053_modulo_pj.sql",
              "057_natureza_lancamento.sql", "064_clientes_lojista.sql",
              "066_pessoas_identidade.sql", "067_titulos_cliente.sql",
              "131_pessoa_cnpj.sql"]


@pytest.fixture(scope="module")
def pool():
    url = os.environ["TEST_DATABASE_URL"]
    p = ConnectionPool(url, min_size=2, max_size=8, open=True)
    init_schema(p)
    mig_dir = Path(__file__).resolve().parents[1] / "db" / "migracoes"
    with p.connection() as c:
        for nome in _MIGRACOES:
            c.execute((mig_dir / nome).read_text(encoding="utf-8"))
        c.commit()
    with p.connection() as c:
        _garantir_orcamentos(c)
    yield p
    p.close()


def _conta16(cid):
    """Tupla mínima no formato de conta_logada (só os índices que o helper lê:
    [0]=conta_id)."""
    t = [None] * 16
    t[0] = cid
    return t


@pytest.fixture()
def conta_id(pool):
    with pool.connection() as c:
        cid = c.execute(
            "insert into contas (tipo, nome, status) values ('pj','TESTE-DASH','trial') returning id"
        ).fetchone()[0]
        c.commit()
    yield cid
    with pool.connection() as c:
        c.execute("delete from orcamentos where conta_id=%s", (cid,))
        c.execute("delete from lancamentos where conta_id=%s", (cid,))
        c.execute("delete from titulos where conta_id=%s", (cid,))
        c.execute("delete from contas where id=%s", (cid,))
        c.commit()


def _seed(pool, cid):
    hoje = date.today()
    # títulos
    emp.criar_titulo(pool, cid, "receber", "Consultoria", 1_000_000, hoje + timedelta(days=10))
    emp.criar_titulo(pool, cid, "receber", "Assinatura", 360_000, hoje + timedelta(days=15),
                     recorrente=True)
    emp.criar_titulo(pool, cid, "pagar", "Fornecedor", 300_000, hoje + timedelta(days=5))
    emp.criar_titulo(pool, cid, "pagar", "Atrasado", 120_000, hoje - timedelta(days=3))
    # lançamentos do mês (natureza=empresa entra no DRE)
    with pool.connection() as c:
        for tipo, cat, val in [("receita", "Consultoria IA", 800_000),
                               ("despesa", "API", 200_000)]:
            c.execute(
                """insert into lancamentos
                     (conta_id, tipo, valor_centavos, categoria, descricao, data,
                      origem, natureza)
                   values (%s,%s,%s,%s,%s,%s,'teste','empresa')""",
                (cid, tipo, val, cat, cat, hoje))
        # propostas: 2 rascunho, 3 enviado, 2 fechado (c/ valor), 1 perdido
        def _orc(status, setup=0, mensal=0):
            c.execute(
                "insert into orcamentos (conta_id, cliente, status, setup_centavos, mensal_centavos) "
                "values (%s,'Cliente',%s,%s,%s)", (cid, status, setup, mensal))
        for _ in range(2): _orc("rascunho")
        for _ in range(3): _orc("enviado", 200_000, 10_000)
        for _ in range(2): _orc("fechado", 500_000, 30_000)
        _orc("perdido", 100_000, 5_000)
        c.commit()


def test_dashboard_numeros(pool, conta_id):
    _seed(pool, conta_id)
    d = portal._painel_dashboard(pool, _conta16(conta_id), vende_servico=True)

    assert d["tem_funil"] is True

    # resumo de títulos (janela 30d)
    assert d["res"]["a_receber_centavos"] == 1_360_000
    assert d["res"]["n_receber"] == 2
    assert d["res"]["a_pagar_centavos"] == 300_000
    assert d["res"]["atrasados_pagar_centavos"] == 120_000

    # MRR = recorrente aberto a receber
    assert d["mrr_centavos"] == 360_000

    # DRE do mês
    assert d["dre"]["receitas_centavos"] == 800_000
    assert d["dre"]["despesas_centavos"] == 200_000
    assert d["dre"]["resultado_centavos"] == 600_000

    # funil: 8 propostas, 2 fechadas → 25%
    assert d["funil_total"] == 8
    assert d["funil_conversao"] == 25
    # valor previsto = setup+12*mensal das NÃO perdidas
    # enviado: 3*(200k+120k)=960k ; fechado: 2*(500k+360k)=1720k → 2_680_000
    assert d["funil_previsto"] == 2_680_000
    # novo recorrente/mês = mensal das fechadas = 60_000
    assert d["funil_recorrente"] == 60_000
    # legenda tem os 4 status presentes; donut é um conic-gradient
    assert len(d["funil_legenda"]) == 4
    assert d["funil_gradiente"].startswith("conic-gradient(")

    # série do fluxo projetado (5 pontos: hoje + 4 semanas)
    assert d["fluxo_poly"].count(",") == 5
    assert d["fluxo_area"].startswith("M")


def test_dashboard_conta_vazia(pool, conta_id):
    """Conta de serviço sem nada: dashboard zerado, funil vazio, sem quebrar."""
    d = portal._painel_dashboard(pool, _conta16(conta_id), vende_servico=True)
    assert d["res"]["a_receber_centavos"] == 0
    assert d["mrr_centavos"] == 0
    assert d["tem_funil"] is True
    assert d["funil_total"] == 0
    assert d["funil_legenda"] == []
    assert d["funil_gradiente"] == ""
    # fluxo ainda produz série (saldo 0 constante)
    assert d["fluxo_poly"].count(",") == 5


def test_dashboard_produto_only(pool, conta_id):
    """Conta que vende PRODUTO mas não serviço: financeiro preenchido, SEM funil.

    Segmentação por nicho: mesmo com propostas na tabela, produto puro não vê o
    funil (tem_funil False) e a query nem roda."""
    _seed(pool, conta_id)  # tem orcamentos, mas a conta não vende serviço
    d = portal._painel_dashboard(pool, _conta16(conta_id), vende_servico=False)
    # financeiro segue universal
    assert d["res"]["a_receber_centavos"] == 1_360_000
    assert d["mrr_centavos"] == 360_000
    assert d["dre"]["resultado_centavos"] == 600_000
    # funil desligado pelo segmento
    assert d["tem_funil"] is False
    assert d["funil_total"] == 0
    assert d["funil_legenda"] == []
    assert d["funil_gradiente"] == ""
