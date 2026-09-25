"""A visita conta igual no Raio-X e no Relatório → Funil (finance/visita.py).

O CASO QUE TROUXE ISTO (24/09/2026, Prime, conta 34). O gestor pôs as duas telas
lado a lado pro Pedro Yan em setembro: 4 visitas no Relatório, "3 marcadas / 2
realizadas / 1 sem resposta" no Raio-X. A que faltava era a "Visita Técnica -
Renata", digitada na Agenda sem card — o Raio-X só via visita com card. E o
Relatório dava a visita pra quem marcou; o Raio-X, pro dono do card.

O QUE SE PROVA AQUI, num cenário só, nos dois nichos:
 1. a visita SEM CARD conta nas duas telas, pra quem marcou;
 2. a visita do card do Pedro marcada pelo GESTOR conta pro Pedro nas duas;
 3. a visita num card SEM DONO fica sem dono — não cai em quem marcou;
 4. festa nunca é visita — nem a com tipo, nem a festa digitada solta;
 5. quem vende festa conta só pelo título; quem não vende conta também o
    compromisso ligado a card (a reunião) — e "Reunião" solta nunca;
 6. cancelada e fornecedor não contam;
 7. a que ainda vai acontecer entra no Relatório e no rodapé do Raio-X;
 8. o bloco "Da visita ao contrato" conta a mesma visita, com a sem card à parte
    e com o nome tirado do título sem virar o nome do vendedor;
 9. a espécie em Python e em SQL dizem a mesma coisa, compromisso por compromisso.
"""
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from psycopg_pool import ConnectionPool

from finance import raio_x_dono as rxd
from finance import raio_x_perfil as rxp
from finance import visita as vis
from tests.test_raio_x_dono import _SQL, MIG
from web import painel_relatorios as rel

EVENTOS = rxp.perfil("eventos")
RECORRENTE = rxp.perfil("consultoria")
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
        sem_dono = lead("Sem Dono", None)

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
            # 3. num card sem vendedor, marcada pelo Pedro: a visita fica sem dono
            "orfa": ev("Visita — Sem Dono", _dt(15, 9, 10), membro=pedro, lead_id=sem_dono,
                       desfecho="realizado"),
            # 4. a equipe batiza com o nome do vendedor: conta pro Pedro, e o nome não vira "PEDRO"
            "pedro_no_titulo": ev("VISITA TÉCNICA - PEDRO", _dt(16, 9, 15), membro=pedro,
                                  desfecho="realizado"),
            # 5. festas: com tipo e ligada ao card; e a digitada solta, sem tipo
            "festa_com_tipo": ev("Aniversário — Renata", _dt(20, 9, 18), membro=pedro, lead_id=renata,
                                 tipo_evento="Aniversário"),
            "festa_solta": ev("ANIVERSÁRIO", _dt(20, 9, 12), membro=pedro),
            # 6. reunião solta (de dentro) × reunião ligada ao card da Clara
            "reuniao_solta": ev("Reunião alinhamento marketing", _dt(8, 9, 9), membro=jaque),
            "reuniao_no_card": ev("Reunião com a Clara", _dt(9, 9, 15), membro=jaque, lead_id=clara,
                                  tipo="pessoal"),
            # 7. cancelada e fornecedor
            "cancelada": ev("Visita — Clara", _dt(10, 9, 15), membro=jaque, lead_id=clara, status="cancelado"),
            "fornecedor": ev("Visita fornecedor do buffet", _dt(11, 9, 15), membro=jaque, tipo="fornecedor"),
            # 8. a que ainda vai acontecer (relativa a agora, pra o teste não vencer)
            "futura": ev("Visita — Clara de novo", datetime.now(BRT) + timedelta(days=2), membro=jaque,
                         lead_id=clara),
        }
        c.commit()
    return {"conta": conta, "pedro": pedro, "jaque": jaque, "gestor": gestor, "ids": ids}


@pytest.fixture()
def festa(monkeypatch):
    """A conta vende festa (a Prime). O banco daqui não tem a tabela de nichos."""
    monkeypatch.setattr(vis, "vende_festa", lambda pool, conta_id: True)
    return EVENTOS


@pytest.fixture()
def servico(monkeypatch):
    """A mesma agenda numa conta que vende serviço (a ZAQ)."""
    monkeypatch.setattr(vis, "vende_festa", lambda pool, conta_id: False)
    return RECORRENTE


def _f(perfil, vendedor=None):
    ate = (datetime.now(BRT) + timedelta(days=10)).date()
    return rxd.filtros({"periodo": "datas", "de": "2026-09-01", "ate": ate.isoformat(),
                        **({"vendedor": str(vendedor)} if vendedor else {})}, perfil)


def _raio_x(pool, cen, perfil, vendedor=None):
    p = rxd.dono(pool, cen["conta"], _f(perfil, vendedor), perfil=perfil)["placar"]
    return p["visitas_ok"], p["visitas_nao"], p["visitas_sem_resposta"], p["visitas_futuras"]


def _relatorio(pool, cen, vendedor=None):
    d = rel._dados_funil(pool, cen["conta"], "todos", "", vendedor or "", "")
    return d["linhas"]


# ───────────────────────────── quem vende festa (a Prime)

def test_o_caso_da_prime_as_duas_telas_dao_o_mesmo_numero(pool, cen, festa):
    # sem card (Renata), no card dele pelo gestor (Agna), e a do título com o nome dele
    assert _raio_x(pool, cen, festa, cen["pedro"]) == (3, 0, 0, 0)
    linhas = _relatorio(pool, cen, cen["pedro"])
    assert len(linhas) == 3
    assert {x["vendedor"] for x in linhas} == {"Pedro Yan"}


def test_a_visita_do_card_e_do_dono_do_card_mesmo_marcada_por_outro(pool, cen, festa):
    assert _raio_x(pool, cen, festa, cen["gestor"]) == (0, 0, 0, 0)
    assert _relatorio(pool, cen, cen["gestor"]) == []


def test_na_festa_a_reuniao_no_card_nao_e_visita_e_a_futura_entra_nas_duas(pool, cen, festa):
    assert _raio_x(pool, cen, festa, cen["jaque"]) == (0, 0, 0, 1)
    assert len(_relatorio(pool, cen, cen["jaque"])) == 1


def test_sem_filtro_a_soma_do_raio_x_e_a_lista_do_relatorio(pool, cen, festa):
    ok, nao, sem, fut = _raio_x(pool, cen, festa)
    assert (ok, nao, sem, fut) == (4, 0, 0, 1)
    assert ok + nao + sem + fut == len(_relatorio(pool, cen))


def test_card_sem_dono_nao_da_a_visita_pra_quem_marcou(pool, cen, festa):
    orfa = [x for x in _relatorio(pool, cen) if x["lead"] == "Sem Dono"]
    assert len(orfa) == 1 and orfa[0]["vendedor"] == "—"


def test_o_nome_da_visita_sem_card_sai_do_titulo_sem_virar_o_vendedor(pool, cen, festa):
    nomes = {x["lead"] for x in _relatorio(pool, cen, cen["pedro"])}
    assert nomes == {"Renata", "Agna Luíza", "VISITA TÉCNICA - PEDRO"}


def test_da_visita_ao_contrato_conta_a_mesma_visita_e_separa_a_sem_card(pool, cen, festa):
    ini = datetime(2026, 9, 1, tzinfo=BRT)
    fim = datetime.now(BRT) + timedelta(days=10)
    with pool.connection() as c:
        dv = rxd.da_visita(c, cen["conta"], {}, ini, fim, festa=True)
        dv_pedro = rxd.da_visita(c, cen["conta"], {"vendedor": cen["pedro"]}, ini, fim, festa=True)
    assert dv["visitas"] == 4                      # Agna e Sem Dono (card) + as duas sem card
    assert dv["sem_card"] == ["Renata", "VISITA TÉCNICA - PEDRO"]
    assert dv["sem_orcamento"] == ["Agna Luíza", "Sem Dono"]   # a sem card não cai aqui
    assert dv["por_vendedor"][cen["pedro"]]["visitas"] == 3
    assert dv["por_vendedor"][None]["visitas"] == 1            # a do card vago: "Outros"
    assert dv_pedro["visitas"] == 3
    assert dv["marcadas"] == 4                     # pessoas, não linhas


def test_filtro_de_lead_tira_a_visita_sem_card(pool, cen, festa):
    """Filtro de festa/origem pergunta "tem um lead assim"; a sem card não tem."""
    with pool.connection() as c:
        c.execute("update prospeccao set evento_tipo='Casamento' where conta_id=%s", (cen["conta"],))
        c.commit()
    try:
        f = _f(festa)
        f["tipo"] = "Casamento"
        p = rxd.dono(pool, cen["conta"], f, perfil=festa)["placar"]
        assert (p["visitas_ok"], p["visitas_sem_resposta"], p["visitas_futuras"]) == (2, 0, 1)
    finally:
        with pool.connection() as c:
            c.execute("update prospeccao set evento_tipo=null where conta_id=%s", (cen["conta"],))
            c.commit()


def test_o_resumo_semanal_conta_pela_mesma_regua(pool, cen, festa):
    """A linha de cada vendedor no resumo do grupo: a sem card e a marcada pelo
    gestor no card dele são do Pedro, como no Raio-X e no Relatório."""
    from finance import resumo_semanal as rs
    ini = datetime(2026, 9, 1, tzinfo=BRT)
    fim = datetime.now(BRT) + timedelta(days=10)
    ex = rs._extras(pool, cen["conta"], ini, fim, datetime.now(BRT))
    visitas = {v["id"]: v["visitas"] for v in ex["por_vendedor"]}
    assert visitas[cen["pedro"]] == 3
    assert visitas[cen["jaque"]] == 0


# ───────────────────────────── quem vende serviço (a ZAQ)

def test_no_servico_a_reuniao_ligada_ao_card_conta_nas_duas(pool, cen, servico):
    assert _raio_x(pool, cen, servico, cen["jaque"]) == (0, 0, 1, 1)
    assert len(_relatorio(pool, cen, cen["jaque"])) == 2


def test_no_servico_a_soma_tambem_bate(pool, cen, servico):
    ok, nao, sem, fut = _raio_x(pool, cen, servico)
    assert ok + nao + sem + fut == len(_relatorio(pool, cen))


# ───────────────────────────── a espécie

@pytest.mark.parametrize("vende_festa", [True, False])
def test_a_especie_em_python_e_em_sql_dizem_o_mesmo(pool, cen, vende_festa):
    with pool.connection() as c:
        rows = c.execute(
            f"select id, titulo, tipo_evento, tipo, prospeccao_id, {vis.sql_e_visita('e', festa=vende_festa)} "
            "from eventos_agenda e where e.conta_id=%s", (cen["conta"],)).fetchall()
    assert len(rows) == len(cen["ids"])
    for _id, titulo, tipo_evento, tipo, lead_id, no_sql in rows:
        assert vis.eh_visita(titulo=titulo, tipo_evento=tipo_evento, tipo=tipo,
                             prospeccao_id=lead_id, festa=vende_festa) == no_sql, titulo
    ids = cen["ids"]
    esperado = {ids["renata_sem_card"], ids["agna_pelo_gestor"], ids["orfa"], ids["pedro_no_titulo"],
                ids["cancelada"], ids["futura"]}        # a cancelada é visita; só não conta
    if not vende_festa:
        esperado |= {ids["reuniao_no_card"]}
    assert {i for i, *_x, s in rows if s} == esperado


def test_retornar_contato_e_festa_nunca_sao_visita():
    assert not vis.eh_visita(titulo="Retornar contato: Ana", prospeccao_id=10)
    assert vis.eh_visita(titulo="  VISITA TÉCNICA - PEDRO", festa=True)
    assert not vis.eh_visita(titulo="Visita — Ana", tipo_evento="Casamento", prospeccao_id=1)
    assert not vis.eh_visita(titulo="Reunião com Paulo")
    assert vis.eh_visita(titulo="Reunião com Paulo", prospeccao_id=3)
    # a festa digitada sem tipo e ligada ao card: na festa, não é visita
    assert not vis.eh_visita(titulo="Formatura - Beatriz", prospeccao_id=3, festa=True)


def test_o_card_do_formulario_so_liga_se_for_da_conta_e_do_vendedor(pool, cen):
    """O `prospeccao_id` vem do navegador. O vendedor só liga card DELE (senão
    daria a visita ao colega no Raio-X); dono e gestor, qualquer card da conta;
    o financeiro, nenhum; card de outra conta, nunca."""
    from web import painel_agenda as pa
    with pool.connection() as c:
        agna = c.execute("select id from prospeccao where contato='Agna Luíza' and conta_id=%s",
                         (cen["conta"],)).fetchone()[0]
    def ctx(papel, membro, conta=None):
        return {"conta_id": conta or cen["conta"], "papel": papel, "membro_id": membro}
    assert pa._card_do_compromisso(pool, ctx("vendedor", cen["pedro"]), str(agna)) == agna
    assert pa._card_do_compromisso(pool, ctx("vendedor", cen["jaque"]), str(agna)) is None
    assert pa._card_do_compromisso(pool, ctx("gestor", cen["gestor"]), str(agna)) == agna
    assert pa._card_do_compromisso(pool, ctx("dono", None), str(agna)) == agna
    assert pa._card_do_compromisso(pool, ctx("financeiro", cen["gestor"]), str(agna)) is None
    assert pa._card_do_compromisso(pool, ctx("dono", None, conta=cen["conta"] + 99), str(agna)) is None
    assert pa._card_do_compromisso(pool, ctx("dono", None), "x1") is None
