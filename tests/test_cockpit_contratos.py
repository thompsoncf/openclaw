"""O cockpit conta a venda pelo CONTRATO ASSINADO (aprovado pelo dono em 24/09/2026,
docs/mockups/prime_cockpit_contratos.html).

Na Prime o cockpit dizia "8 negócios" em setembro e o relatório de Contratos, 11
contratos: o cockpit contava lead no CONTRATO ASSINADO, e dois contratos foram
feitos direto pelo orçamento, sem lead (Josinalva e Viviane). Agora, na conta que
já assinou contrato:

- o "fechado no período" são os contratos assinados no período, pelo valor deles;
- o funil soma os contratos sem lead na etapa de fechamento, e diz quantos;
- o placar dá o contrato a quem fez o orçamento (na falta, ao vendedor do lead).

Conta sem contrato nenhum segue pelo lead, como antes (test_cockpit_dono).
"""
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from finance import cockpit_dono as cd
from tests.test_cockpit import _BASE_SQL

_SQL = _BASE_SQL + """
create table orcamentos (id bigserial primary key, conta_id bigint, empresa text, cliente text,
  criado_por text, canal text, status text, setup_centavos bigint default 0,
  mensal_centavos bigint default 0, primeiro_ano_centavos bigint, criado_em timestamptz default now());
alter table contratos add column if not exists substitui_id bigint;
"""


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_cockpit_contratos_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        mig = Path(__file__).resolve().parents[1] / "db" / "migracoes"
        for nome in ("209_raio_x_dono.sql", "213_perda_motivo_por_perfil.sql",
                     "235_motivos_de_perda_da_conta.sql", "254_funil_semeado_de.sql",
                     "328_perda_lida_da_conversa.sql", "331_perda_lida_detalhe.sql"):
            c.execute((mig / nome).read_text(encoding="utf-8"))
        c.commit()
    yield p
    p.close()


def _prime(c):
    """Jacqueline fez 2 orçamentos (um com lead do Thiago, um sem lead); Thiago fez
    1, pro lead dele. Mais um lead ganho SEM contrato, um rescindido e um aditivo."""
    agora = datetime.now(cd._brt())
    conta = c.execute("insert into contas (nome, nome_fantasia) values ('C','Prime') returning id").fetchone()[0]
    for ch, rot, fase, ordem in (("proposta", "Negociação", "venda", 1),
                                 ("ganho", "CONTRATO ASSINADO", "fechamento", 2),
                                 ("perdido", "Perdido", "fechamento", 3)):
        c.execute("insert into funil_etapas (conta_id, chave, rotulo, fase, ordem) values (%s,%s,%s,%s,%s)",
                  (conta, ch, rot, fase, ordem))
    dono = c.execute("insert into membros (conta_id,nome,email,papel) values (%s,'Manoel','m@x.com','dono') "
                     "returning id", (conta,)).fetchone()[0]
    jac = c.execute("insert into membros (conta_id,nome,email,papel) values (%s,'Jacqueline','j@x.com','vendedor') "
                    "returning id", (conta,)).fetchone()[0]
    thi = c.execute("insert into membros (conta_id,nome,email,papel) values (%s,'Thiago','t@x.com','vendedor') "
                    "returning id", (conta,)).fetchone()[0]

    def orc(nome, criado_por):
        return c.execute("insert into orcamentos (conta_id, empresa, criado_por, status, primeiro_ano_centavos) "
                         "values (%s,%s,%s,'fechado',1) returning id", (conta, nome, criado_por)).fetchone()[0]

    def lead(nome, vend, o=None, status="ganho"):
        return c.execute("insert into prospeccao (conta_id,vendedor_id,empresa,status,estagio,orcamento_id) "
                         "values (%s,%s,%s,%s,'lead',%s) returning id", (conta, vend, nome, status, o)).fetchone()[0]

    def ct(o, valor, quando, status="assinado", substitui=None):
        c.execute("insert into contratos (conta_id, orcamento_id, status, valor_centavos, assinado_em, substitui_id) "
                  "values (%s,%s,%s,%s,%s,%s)", (conta, o, status, valor, quando, substitui))

    o1 = orc("Bianca", str(jac))          # a Jacqueline fez o orçamento do lead do Thiago
    lead("Bianca", thi, o1)
    ct(o1, 500000, agora - timedelta(hours=2))
    ct(orc("Josinalva Silva", str(jac)), 200000, agora - timedelta(hours=1))   # sem lead
    o3 = orc("Carla", "dono")             # sem autor numérico: vale o vendedor do lead
    lead("Carla", thi, o3)
    ct(o3, 300000, agora - timedelta(hours=3))
    lead("Sem contrato", thi)             # ganho sem contrato: não é venda aqui
    ct(orc("Rescindido", str(jac)), 999900, agora - timedelta(hours=1), status="rescindido")
    ct(orc("Aditivo", str(jac)), 888800, agora - timedelta(hours=1), substitui=1)
    lead("Aberto", thi, status="proposta")
    c.commit()
    return conta, dono, jac, thi


def test_visao_conta_contratos_assinados(pool):
    with pool.connection() as c:
        conta, *_ = _prime(c)
    k = cd.visao(pool, conta, "semana")["kpis"]
    assert k["por_contrato"] is True
    assert k["ganhos"] == 3 and k["ganhos_rs"] == "R$ 10 mil"
    # conversão continua ganhos ÷ leads novos (4 leads na semana)
    assert k["conversao"] == 75


def test_funil_soma_o_contrato_sem_lead_e_diz_quantos(pool):
    with pool.connection() as c:
        conta, *_ = _prime(c)
    fun = {f["chave"]: f for f in cd.visao(pool, conta, "semana")["funil"]}
    g = fun["ganho"]
    assert g["n"] == 4                      # 3 leads ganhos + 1 contrato sem lead
    assert g["sem_lead"] == 1 and g["sem_lead_nomes"] == ["Josinalva"]
    assert fun["perdido"]["sem_lead"] == 0 and fun["proposta"]["sem_lead"] == 0


def test_placar_da_o_contrato_a_quem_fez_o_orcamento(pool, monkeypatch):
    # o placar é "este mês": no dia 1º antes das 3h, os contratos de "3 horas atrás"
    # cairiam no mês anterior. A janela de 7 dias tira o relógio do teste.
    agora = datetime.now(cd._brt())
    monkeypatch.setattr(cd, "_range", lambda periodo="semana", de=None, ate=None:
                        (agora - timedelta(days=7), agora + timedelta(minutes=1)))
    with pool.connection() as c:
        conta, dono, jac, thi = _prime(c)
    lista = cd.placar(pool, conta)
    por = {x["nome"]: x for x in lista}
    assert por["Jacqueline"]["ganhos"] == 2 and por["Jacqueline"]["rs_centavos"] == 700000
    assert por["Jacqueline"]["sem_lead"] == 1 and por["Jacqueline"]["por_contrato"] is True
    assert por["Thiago"]["ganhos"] == 1 and por["Thiago"]["rs_centavos"] == 300000
    assert lista[0]["nome"] == "Jacqueline"
    v = cd.vendedor(pool, conta, jac)
    assert v["ganhos"] == 2 and v["sem_lead"] == 1 and v["por_contrato"] is True


def test_conta_sem_contrato_segue_pelo_lead(pool):
    with pool.connection() as c:
        conta = c.execute("insert into contas (nome, nome_fantasia) values ('C','Sem') returning id").fetchone()[0]
        v = c.execute("insert into membros (conta_id,nome,email,papel) values (%s,'Ana','a@x.com','vendedor') "
                      "returning id", (conta,)).fetchone()[0]
        c.execute("insert into prospeccao (conta_id,vendedor_id,empresa,status,estagio,valor_estimado_centavos) "
                  "values (%s,%s,'X','ganho','lead',400000)", (conta, v))
        c.commit()
    k = cd.visao(pool, conta, "semana")["kpis"]
    assert k["por_contrato"] is False and k["ganhos"] == 1 and k["ganhos_rs"] == "R$ 4 mil"
    assert cd.placar(pool, conta)[0]["por_contrato"] is False


def _r(**kw):
    base = {"recebido_centavos": 0, "n_vendas": 0, "comissao_pct": None, "comissao_centavos": None,
            "fechado_centavos": 700000, "ganhos": 2, "conversao": "3%", "recebidos": 70,
            "resp": "—", "fila": 4, "posicao": 1, "total_equipe": 3}
    base.update(kw)
    return base


def test_o_bloco_de_dinheiro_do_vendedor_fala_de_contrato_na_conta_com_contrato():
    # o número agora é o de CONTRATOS ASSINADOS — "ganho(s) · previsão" mentiria
    from web import painel_cockpit as pc
    html = pc._bloco_dinheiro(_r(por_contrato=True, sem_lead=1))
    assert "Contratos assinados" in html and "2 contrato(s) · 1 sem lead" in html
    assert "previsão" not in html and "No funil" not in html
    assert "2 contrato(s) de 70 leads recebidos" in html


def test_o_bloco_de_dinheiro_sem_contrato_segue_como_era():
    from web import painel_cockpit as pc
    html = pc._bloco_dinheiro(_r())
    assert "No funil" in html and "2 ganho(s) · previsão" in html
    assert "2 fechado(s) de 70 leads recebidos" in html
