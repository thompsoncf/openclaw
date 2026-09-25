"""A visita conta igual no Raio-X e no Relatório → Funil (finance/visita.py).

O CASO QUE TROUXE ISTO (24/09/2026, Prime, conta 34). O gestor pôs as duas telas
lado a lado pro Pedro Yan em setembro: 4 visitas no Relatório, "3 marcadas / 2
realizadas / 1 sem resposta" no Raio-X. A que faltava era a "Visita Técnica -
Renata", digitada na Agenda sem card — o Raio-X só via visita com card. E o
Relatório dava a visita pra quem marcou; o Raio-X, pro dono do card.

O QUE SE PROVA AQUI, num cenário só:
 1. a visita SEM CARD conta nas duas telas, pra quem marcou;
 2. a visita do card do Pedro marcada pelo GESTOR conta pro Pedro nas duas;
 3. festa nunca é visita — nem a com tipo, nem a festa digitada solta;
 4. "Reunião" solta não é visita (é reunião de dentro); ligada a card é;
 5. cancelada e fornecedor não contam;
 6. a que ainda vai acontecer entra no Relatório e no rodapé do Raio-X;
 7. o bloco "Da visita ao contrato" conta a mesma visita, com a sem card à parte;
 8. a espécie em Python e em SQL dizem a mesma coisa, compromisso por compromisso.
"""
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from psycopg_pool import ConnectionPool

from finance import raio_x_dono as rxd
from finance import raio_x_perfil as rxp
from finance import visita as vis
from tests.test_raio_x_dono import _SQL, MIG
from web import painel_relatorios as rel

EVENTOS = rxp.perfil("eventos")
BRT = ZoneInfo("America/Sao_Paulo")


def _dt(d, m, h):
    return datetime(2026, m, d, h, 0, tzinfo=BRT)


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True,
                           kwargs={"autocommit": True, "prepare_threshold": None})
    dbname = "zaq_visita_regua_test"
    with admin.connection() as c:
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity "
                  "where datname=%s and pid <> pg_backend_pid()", (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        c.execute((MIG / "209_raio_x_dono.sql").read_text(encoding="utf-8"))
        c.execute((MIG / "213_perda_motivo_por_perfil.sql").read_text(encoding="utf-8"))
        # o que o Relatório → Funil lê e o esquema do Raio-X não precisava
        c.execute("alter table eventos_agenda add column criado_em timestamptz default now()")
        c.commit()
    yield p
    p.close()


@pytest.fixture(scope="module")
def cen(pool):
    with pool.connection() as c:
        conta = c.execute("insert into contas (nome) values ('Prime') returning id").fetchone()[0]
        pedro = c.execute("insert into membros (conta_id, nome) values (%s,'Pedro Yan') returning id",
                          (conta,)).fetchone()[0]
        jaque = c.execute("insert into membros (conta_id, nome) values (%s,'Jacqueline') returning id",
                          (conta,)).fetchone()[0]
        gestor = c.execute("insert into membros (conta_id, nome, papel) values (%s,'Gestor','gestor') returning id",
                           (conta,)).fetchone()[0]

        def lead(nome, vend):
            return c.execute("insert into prospeccao (conta_id, vendedor_id, contato, criado_em) "
                             "values (%s,%s,%s,%s) returning id",
                             (conta, vend, nome, _dt(1, 9, 9))).fetchone()[0]

        renata, agna, clara = lead("Renata Costa", pedro), lead("Agna Luíza", pedro), lead("Clara", jaque)

        def ev(titulo, inicio, *, membro, lead_id=None, tipo="empresa", tipo_evento=None,
               status="ativo", desfecho=None):
            return c.execute(
                "insert into eventos_agenda (conta_id, membro_id, prospeccao_id, titulo, inicio, tipo,"
                " tipo_evento, status, desfecho) values (%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id",
                (conta, membro, lead_id, titulo, inicio, tipo, tipo_evento, status, desfecho)).fetchone()[0]

        ids = {
            # 1. o caso da Prime: digitada na Agenda, sem card, tipo pessoal (o padrão)
            "renata_sem_card": ev("Visita Técnica - Renata", _dt(5, 9, 17), membro=pedro,
                                  tipo="pessoal", desfecho="realizado"),
            # 2. no card do Pedro, marcada pelo gestor
            "agna_pelo_gestor": ev("Visita — Agna Luíza", _dt(12, 9, 10), membro=gestor, lead_id=agna,
                                   desfecho="realizado"),
            # 3. festas: com tipo e ligada ao card; e a digitada solta, sem tipo
            "festa_com_tipo": ev("Aniversário — Renata", _dt(20, 9, 18), membro=pedro, lead_id=renata,
                                 tipo_evento="Aniversário"),
            "festa_solta": ev("ANIVERSÁRIO", _dt(20, 9, 12), membro=pedro),
            # 4. reunião solta (de dentro) × reunião ligada ao card da Clara
            "reuniao_solta": ev("Reunião alinhamento marketing", _dt(8, 9, 9), membro=jaque),
            "reuniao_no_card": ev("Reunião com a Clara", _dt(9, 9, 15), membro=jaque, lead_id=clara,
                                  tipo="pessoal"),
            # 5. cancelada e fornecedor
            "cancelada": ev("Visita — Clara", _dt(10, 9, 15), membro=jaque, lead_id=clara, status="cancelado"),
            "fornecedor": ev("Visita fornecedor do buffet", _dt(11, 9, 15), membro=jaque, tipo="fornecedor"),
            # 6. a que ainda vai acontecer (relativa a agora, pra o teste não vencer)
            "futura": ev("Visita — Clara de novo", datetime.now(BRT) + timedelta(days=2), membro=jaque,
                         lead_id=clara),
        }
        c.commit()
    return {"conta": conta, "pedro": pedro, "jaque": jaque, "gestor": gestor, "ids": ids}


def _f(vendedor=None):
    ate = (datetime.now(BRT) + timedelta(days=10)).date()
    return rxd.filtros({"periodo": "datas", "de": "2026-09-01", "ate": ate.isoformat(),
                        **({"vendedor": str(vendedor)} if vendedor else {})}, EVENTOS)


def _raio_x(pool, cen, vendedor=None):
    p = rxd.dono(pool, cen["conta"], _f(vendedor), perfil=EVENTOS)["placar"]
    return p["visitas_ok"], p["visitas_nao"], p["visitas_sem_resposta"], p["visitas_futuras"]


def _relatorio(pool, cen, vendedor=None):
    d = rel._dados_funil(pool, cen["conta"], "todos", "", vendedor or "", "")
    return d["linhas"]


def test_o_caso_da_prime_as_duas_telas_dao_4_e_nao_3_e_4(pool, cen):
    # sem card (Renata) + no card dele marcada pelo gestor (Agna): 2 realizadas
    assert _raio_x(pool, cen, cen["pedro"]) == (2, 0, 0, 0)
    linhas = _relatorio(pool, cen, cen["pedro"])
    assert len(linhas) == 2
    assert {x["vendedor"] for x in linhas} == {"Pedro Yan"}


def test_a_visita_do_card_e_do_dono_do_card_mesmo_marcada_por_outro(pool, cen):
    assert _raio_x(pool, cen, cen["gestor"]) == (0, 0, 0, 0)
    assert _relatorio(pool, cen, cen["gestor"]) == []


def test_reuniao_so_conta_ligada_ao_card_e_a_futura_entra_nas_duas(pool, cen):
    # Jaque: a reunião no card da Clara (passou, sem desfecho) + a futura
    assert _raio_x(pool, cen, cen["jaque"]) == (0, 0, 1, 1)
    assert len(_relatorio(pool, cen, cen["jaque"])) == 2


def test_sem_filtro_a_soma_do_raio_x_e_a_lista_do_relatorio(pool, cen):
    ok, nao, sem, fut = _raio_x(pool, cen)
    assert (ok, nao, sem, fut) == (2, 0, 1, 1)
    assert ok + nao + sem + fut == len(_relatorio(pool, cen))


def test_o_nome_da_visita_sem_card_sai_do_titulo(pool, cen):
    nomes = {x["lead"] for x in _relatorio(pool, cen, cen["pedro"])}
    assert "Renata" in nomes


def test_da_visita_ao_contrato_conta_a_mesma_visita_e_separa_a_sem_card(pool, cen):
    ini = datetime(2026, 9, 1, tzinfo=BRT)
    fim = datetime.now(BRT) + timedelta(days=10)
    with pool.connection() as c:
        dv = rxd.da_visita(c, cen["conta"], {}, ini, fim)
        dv_pedro = rxd.da_visita(c, cen["conta"], {"vendedor": cen["pedro"]}, ini, fim)
    assert dv["visitas"] == 2                     # Agna (card) + Renata (sem card)
    assert dv["sem_card"] == ["Renata"]
    assert dv["sem_orcamento"] == ["Agna Luíza"]  # a sem card não cai aqui
    assert dv["por_vendedor"][cen["pedro"]]["visitas"] == 2
    assert dv_pedro["visitas"] == 2
    # marcadas: Renata, Agna e a reunião da Clara (que passou) — pessoas, não linhas
    assert dv["marcadas"] == 3


def test_filtro_de_lead_tira_a_visita_sem_card(pool, cen):
    """Filtro de festa/origem pergunta "tem um lead assim"; a sem card não tem."""
    with pool.connection() as c:
        c.execute("update prospeccao set evento_tipo='Casamento' where conta_id=%s", (cen["conta"],))
        c.commit()
    try:
        f = _f()
        f["tipo"] = "Casamento"
        p = rxd.dono(pool, cen["conta"], f, perfil=EVENTOS)["placar"]
        assert (p["visitas_ok"], p["visitas_sem_resposta"], p["visitas_futuras"]) == (1, 1, 1)
    finally:
        with pool.connection() as c:
            c.execute("update prospeccao set evento_tipo=null where conta_id=%s", (cen["conta"],))
            c.commit()


def test_a_especie_em_python_e_em_sql_dizem_o_mesmo(pool, cen):
    with pool.connection() as c:
        rows = c.execute(
            f"select id, titulo, tipo_evento, tipo, prospeccao_id, {vis.sql_e_visita('e')} "
            "from eventos_agenda e where e.conta_id=%s", (cen["conta"],)).fetchall()
    assert len(rows) == len(cen["ids"])
    for _id, titulo, tipo_evento, tipo, lead_id, no_sql in rows:
        assert vis.eh_visita(titulo=titulo, tipo_evento=tipo_evento, tipo=tipo,
                             prospeccao_id=lead_id) == no_sql, titulo
    e_visita = {i for i, *_x, s in rows if s}
    ids = cen["ids"]
    assert e_visita == {ids["renata_sem_card"], ids["agna_pelo_gestor"], ids["reuniao_no_card"],
                        ids["cancelada"], ids["futura"]}   # a cancelada é visita; só não conta


def test_retornar_contato_nunca_e_visita():
    assert not vis.eh_visita(titulo="Retornar contato: Ana", prospeccao_id=10)
    assert vis.eh_visita(titulo="  VISITA TÉCNICA - PEDRO")
    assert not vis.eh_visita(titulo="Visita — Ana", tipo_evento="Casamento", prospeccao_id=1)
    assert not vis.eh_visita(titulo="Reunião com Paulo")
    assert vis.eh_visita(titulo="Reunião com Paulo", prospeccao_id=3)


def test_o_resumo_semanal_conta_pela_mesma_regua(pool, cen):
    """A linha de cada vendedor no resumo do grupo: a visita sem card e a marcada
    pelo gestor no card dele são do Pedro, como no Raio-X e no Relatório."""
    from finance import resumo_semanal as rs
    ini = datetime(2026, 9, 1, tzinfo=BRT)
    fim = datetime.now(BRT) + timedelta(days=10)
    ex = rs._extras(pool, cen["conta"], ini, fim, datetime.now(BRT))
    visitas = {v["id"]: v["visitas"] for v in ex["por_vendedor"]}
    assert visitas[cen["pedro"]] == 2
    assert visitas[cen["jaque"]] == 0            # a reunião no card da Clara não teve desfecho
