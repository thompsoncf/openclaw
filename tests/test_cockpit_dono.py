"""Cockpit do Dono (finance/cockpit_dono): métricas + ações da equipe.

- visao: KPIs (novos/atendimento/ganhos/conversão), funil do time, precisa de atenção;
- placar: ranking por R$ fechado, com fila/ganhos/conversão/pausado;
- vendedor: drill com leads abertos;
- atividade: feed (ganho/visita/proposta);
- reatribuir: move o lead + a conversa; pausar: liga/desliga o rodízio.

Banco dedicado e descartável (schema do test_cockpit + orcamentos).
"""
import os

import pytest
from psycopg_pool import ConnectionPool

from finance import cockpit_dono as cd
from tests.test_cockpit import _BASE_SQL

_SQL = _BASE_SQL + """
create table orcamentos (id bigserial primary key, conta_id bigint, empresa text, criado_por text,
  canal text, status text, setup_centavos bigint default 0, mensal_centavos bigint default 0,
  criado_em timestamptz default now());
"""


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_cockpit_dono_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        c.commit()
    yield p
    p.close()


def _seed(c, nome="Prime"):
    conta = c.execute("insert into contas (nome, nome_fantasia) values ('C',%s) returning id", (nome,)).fetchone()[0]
    dono = c.execute("insert into membros (conta_id,nome,email,papel) values (%s,'Dono','d@x.com','dono') returning id",
                     (conta,)).fetchone()[0]
    v1 = c.execute("insert into membros (conta_id,nome,email,papel) values (%s,'Carlos','c@x.com','vendedor') returning id",
                   (conta,)).fetchone()[0]
    v2 = c.execute("insert into membros (conta_id,nome,email,papel,cockpit_pausado) values (%s,'Ana','a@x.com','vendedor',true) returning id",
                   (conta,)).fetchone()[0]
    # Carlos: 1 ganho (R$8k) + 1 aberto contatado ; Ana: 1 aberto novo quente
    g = c.execute("insert into prospeccao (conta_id,vendedor_id,empresa,status,estagio,temperatura,valor_estimado_centavos) "
                  "values (%s,%s,'Festa Corp','ganho','lead','quente',800000) returning id", (conta, v1)).fetchone()[0]
    ab = c.execute("insert into prospeccao (conta_id,vendedor_id,empresa,status,estagio,temperatura) "
                   "values (%s,%s,'Bodas','contatado','lead','morno') returning id", (conta, v1)).fetchone()[0]
    an = c.execute("insert into prospeccao (conta_id,vendedor_id,empresa,status,estagio,temperatura) "
                   "values (%s,%s,'Aniversário','novo','lead','quente') returning id", (conta, v2)).fetchone()[0]
    c.execute("insert into conversas (conta_id,prospeccao_id,agente_ativo,responsavel_membro_id) values (%s,%s,false,%s)",
              (conta, ab, v1))
    c.execute("insert into orcamentos (conta_id,empresa,criado_por,canal,status) values (%s,'Festa Corp',%s,'cockpit','enviado')",
              (conta, str(v1)))
    c.execute("insert into eventos_agenda (conta_id,membro_id,prospeccao_id,titulo,inicio,status) "
              "values (%s,%s,%s,'Visita — Bodas',now(),'ativo')", (conta, v1, ab))
    c.commit()
    return conta, dono, v1, v2, ab, an


def test_visao(pool):
    with pool.connection() as c:
        conta, dono, v1, v2, ab, an = _seed(c)
    v = cd.visao(pool, conta, "mes")
    k = v["kpis"]
    assert k["ganhos"] == 1 and k["ganhos_rs"] == "R$ 8 mil" and k["conversao"] == 100
    assert k["com_vend"] == 1 and k["com_ia"] == 1          # Bodas c/ vendedor, Aniversário c/ IA
    fun = {f["rotulo"]: f["n"] for f in v["funil"]}
    assert fun["Novo"] == 1 and fun["Contatado"] == 1 and fun["Qualificado"] == 0   # ganho não entra no funil
    a = v["atencao"]
    assert a["propostas"] == 1 and a["visitas"] == 1 and a["quentes"] == 1


def test_placar_ordena_por_rs(pool):
    with pool.connection() as c:
        conta, dono, v1, v2, ab, an = _seed(c, "Placar")
    lista = cd.placar(pool, conta)
    nomes = [x["nome"] for x in lista]
    assert nomes[0] == "Carlos"                             # mais R$ fechado vem 1º
    carlos = next(x for x in lista if x["nome"] == "Carlos")
    ana = next(x for x in lista if x["nome"] == "Ana")
    assert carlos["ganhos"] == 1 and carlos["rs"] == "R$ 8 mil" and carlos["fila"] == 1
    assert carlos["conversao"] == "100%" and carlos["atendendo"] == 1
    assert ana["pausado"] is True and ana["ganhos"] == 0


def test_vendedor_drill(pool):
    with pool.connection() as c:
        conta, dono, v1, v2, ab, an = _seed(c, "Drill")
    d = cd.vendedor(pool, conta, v1)
    assert d and d["nome"] == "Carlos" and d["fila"] == 1
    assert [l["empresa"] for l in d["leads"]] == ["Bodas"]  # só o aberto (ganho não conta)
    assert cd.vendedor(pool, conta, 999999) is None


def test_atividade_feed(pool):
    with pool.connection() as c:
        conta, dono, v1, v2, ab, an = _seed(c, "Ativ")
    feed = cd.atividade(pool, conta)
    tipos = {e["tipo"] for e in feed}
    assert "ganho" in tipos and "visita" in tipos and "prop" in tipos
    assert any("Carlos ganhou — Festa Corp" in e["txt"] for e in feed)


def test_reatribuir_e_pausar(pool):
    with pool.connection() as c:
        conta, dono, v1, v2, ab, an = _seed(c, "Acao")
    # reatribui o lead do Carlos (ab) pra Ana
    assert cd.reatribuir(pool, conta, ab, v2)["ok"] is True
    assert cd.reatribuir(pool, conta, ab, 999999)["ok"] is False   # destino inválido
    with pool.connection() as c:
        assert c.execute("select vendedor_id from prospeccao where id=%s", (ab,)).fetchone()[0] == v2
        assert c.execute("select responsavel_membro_id from conversas where prospeccao_id=%s", (ab,)).fetchone()[0] == v2
    # pausar / reativar
    cd.pausar(pool, conta, v1, True)
    with pool.connection() as c:
        assert c.execute("select cockpit_pausado from membros where id=%s", (v1,)).fetchone()[0] is True
    cd.pausar(pool, conta, v1, False)
    with pool.connection() as c:
        assert c.execute("select cockpit_pausado from membros where id=%s", (v1,)).fetchone()[0] is False
