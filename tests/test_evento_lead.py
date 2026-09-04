"""O evento no lead (migração 197): parse, gravação sem apagar, agrupamento por mês
e o trilho — a régua de tempo que o funil de eventos não tinha.

POR QUE. Prime Eventos (conta 34), 04/09/2026: 274 leads no funil, 224 numa coluna
só, 65 parados há 15+ dias misturados com os 29 que falaram na semana, e a data da
festa conhecida em 15 — o agente perguntava e guardava só no orçamento. Decisões do
dono no mesmo dia: dobra dos parados em 15 dias; o "perguntar" abre a conversa com o
texto pronto, não dispara sozinho.
"""
import os
from datetime import date, datetime, timedelta, timezone

import pytest
from psycopg_pool import ConnectionPool

from finance import evento_lead as ev

AGORA = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)


# ------------------------------------------------------------------ parse
def test_parse_data_aceita_iso_e_brasileiro():
    assert ev.parse_data("2026-12-31") == date(2026, 12, 31)
    assert ev.parse_data("31/12/2026") == date(2026, 12, 31)
    assert ev.parse_data("31/12/26") == date(2026, 12, 31)
    assert ev.parse_data("2027-01-16T21:00") == date(2027, 1, 16)
    assert ev.parse_data(date(2027, 2, 6)) == date(2027, 2, 6)


def test_parse_data_rejeita_o_que_nao_e_data():
    assert ev.parse_data("") is None
    assert ev.parse_data(None) is None
    assert ev.parse_data("31/02/2026") is None      # não existe
    assert ev.parse_data("dezembro") is None
    assert ev.parse_data("AAAA-MM-DD") is None      # o molde do prompt, sem preencher


def test_parse_convidados_tolera_texto_e_milhar():
    assert ev.parse_convidados("150") == 150
    assert ev.parse_convidados("150 pessoas") == 150
    assert ev.parse_convidados("1.500") == 1500
    assert ev.parse_convidados(120) == 120
    assert ev.parse_convidados("0") is None
    assert ev.parse_convidados("") is None
    assert ev.parse_convidados("muitos") is None


def test_normalizar_so_devolve_o_que_veio_valido():
    assert ev.normalizar({"data": "AAAA-MM-DD", "convidados": 0, "tipo": ""}) == {}
    assert ev.normalizar({"data": "31/12/2026", "convidados": "50", "tipo": " casamento "}) == {
        "data": date(2026, 12, 31), "convidados": 50, "tipo": "casamento"}
    assert ev.normalizar(None) == {}
    assert ev.normalizar("lixo") == {}


# ------------------------------------------------------------------ rótulos
def test_rotulos_de_mes_e_data_curta():
    assert ev.mes_rotulo("2027-01") == "Jan 27"
    assert ev.mes_rotulo("2026-11") == "Nov 26"
    assert ev.mes_intervalo("2026-12") == (date(2026, 12, 1), date(2027, 1, 1))
    assert ev.mes_valido("2027-01") and not ev.mes_valido("2027-13") and not ev.mes_valido("sem")
    hoje = date(2026, 9, 4)
    assert ev.data_curta(date(2026, 11, 28), hoje) == "28 nov"
    assert ev.data_curta(date(2027, 1, 16), hoje) == "16 jan 27"
    assert ev.data_curta(None, hoje) == ""


def test_icone_por_tipo_e_linha_da_ficha():
    assert ev.icone_tipo("Casamento") == "💍"
    assert ev.icone_tipo("15 anos") == "🎂"
    assert ev.icone_tipo("Formatura") == "🎓"
    assert ev.icone_tipo("Confraternização") == "🏢"
    assert ev.icone_tipo(None) == "🎉"
    assert ev.linha_evento("Casamento", date(2026, 11, 14), 150) == "💍 Casamento · 14/11/2026 · 150 convidados"
    assert ev.linha_evento(None, None, None) == ""


# ------------------------------------------------------------------ agrupar
def _card(**kw):
    base = {"evento_em": None, "criado_em": AGORA - timedelta(days=2), "ult_em": None,
            "ultimo_contato_em": None}
    base.update(kw)
    return base


def test_agrupa_por_mes_do_evento_do_mais_proximo_pro_mais_distante():
    jan = _card(id=1, evento_em=date(2027, 1, 16))
    nov = _card(id=2, evento_em=date(2026, 11, 28))
    nov2 = _card(id=3, evento_em=date(2026, 11, 14))
    g = ev.agrupar([jan, nov, nov2], AGORA)
    assert [x["rotulo"] for x in g] == ["Nov 26", "Jan 27"]
    # dentro do mês, pela data da festa
    assert [c["id"] for c in g[0]["cards"]] == [3, 2]
    assert g[0]["n"] == 2 and g[0]["tipo"] == "evento"


def test_sem_data_agrupa_pelo_mes_de_entrada_do_mais_novo_pro_mais_velho():
    set_ = _card(id=1, criado_em=datetime(2026, 9, 2, tzinfo=timezone.utc))
    ago = _card(id=2, criado_em=datetime(2026, 8, 20, tzinfo=timezone.utc),
                ult_em=AGORA - timedelta(days=1))   # falou ontem: não está parado
    g = ev.agrupar([set_, ago], AGORA)
    assert [x["rotulo"] for x in g] == ["Sem data · entrou em set", "Sem data · entrou em ago"]
    assert all(x["tipo"] == "entrada" for x in g)


def test_parado_e_quem_esta_ha_15_dias_sem_mensagem_e_vai_pra_dobra_no_pe():
    """Decisão do dono: 15 dias. Vale a última MENSAGEM em qualquer sentido — quem
    não tem mensagem conta pelo último contato, e sem nada disso pela entrada."""
    quieto = _card(id=1, evento_em=date(2027, 1, 16), ult_em=AGORA - timedelta(days=16),
                   criado_em=AGORA - timedelta(days=30))
    vivo = _card(id=2, evento_em=date(2027, 1, 20), ult_em=AGORA - timedelta(days=14),
                 criado_em=AGORA - timedelta(days=30))
    sem_msg_velho = _card(id=3, criado_em=AGORA - timedelta(days=40))
    sem_msg_contato = _card(id=4, criado_em=AGORA - timedelta(days=40),
                            ultimo_contato_em=AGORA - timedelta(days=3))
    g = ev.agrupar([quieto, vivo, sem_msg_velho, sem_msg_contato], AGORA)
    assert [x["tipo"] for x in g] == ["evento", "entrada", "parado"]
    assert [c["id"] for c in g[0]["cards"]] == [2]
    assert [c["id"] for c in g[1]["cards"]] == [4]
    assert sorted(c["id"] for c in g[2]["cards"]) == [1, 3]
    assert g[2]["rotulo"] == "Parados 15+ dias"


def test_data_ingenua_e_tratada_como_utc_sem_quebrar():
    c = _card(id=1, ult_em=datetime(2026, 9, 3, 10, 0))   # sem fuso
    g = ev.agrupar([c], AGORA)
    assert g[0]["tipo"] == "entrada"


def test_coluna_com_um_grupo_so_de_entrada_nao_ganha_cabecalho():
    g = ev.agrupar([_card(id=1), _card(id=2)], AGORA)
    assert len(g) == 1 and g[0]["rotulo"] == ""


def test_coluna_vazia():
    assert ev.agrupar([], AGORA) == []


# ------------------------------------------------------------------ trilho
def test_trilho_tem_todos_na_frente_meses_em_ordem_e_sem_data_no_fim():
    t = ev.trilho({"2027-01": 3, "2026-11": 2, None: 190}, "2027-01")
    assert [x["rotulo"] for x in t] == ["Todos", "Nov 26", "Jan 27", "Sem data"]
    assert t[0]["n"] == 195 and t[0]["chave"] == "" and not t[0]["on"]
    assert t[2]["on"] and t[2]["chave"] == "2027-01"
    assert t[3]["sem"] and t[3]["n"] == 190 and t[3]["chave"] == "sem"


# ------------------------------------------------------------------ gravar (banco)
_SQL = """
create table prospeccao (id bigserial primary key, conta_id bigint, empresa text,
  orcamento_id bigint, evento_em date, evento_tipo text, evento_convidados int,
  atualizado_em timestamptz default now());
create table orcamentos (id bigserial primary key, conta_id bigint, evento jsonb);
"""


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True,
                           kwargs={"autocommit": True, "prepare_threshold": None})
    dbname = "zaq_evento_lead_test"
    with admin.connection() as c:
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity "
                  "where datname=%s and pid <> pg_backend_pid()", (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=2, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        c.commit()
    yield p
    p.close()


def _lead(c, **kw):
    cols = ["conta_id", "empresa"] + list(kw)
    vals = [34, "Fernanda"] + list(kw.values())
    return c.execute(f"insert into prospeccao ({', '.join(cols)}) values ({', '.join(['%s'] * len(vals))}) "
                     "returning id", vals).fetchone()[0]


def _le(c, lid):
    return c.execute("select evento_em, evento_tipo, evento_convidados from prospeccao where id=%s",
                     (lid,)).fetchone()


def test_gravar_preenche_e_nunca_apaga(pool):
    with pool.connection() as c:
        lid = _lead(c)
        assert ev.gravar(c, 34, lid, {"data": "28/11/2026", "tipo": "Aniversário", "convidados": "120"})
        assert _le(c, lid) == (date(2026, 11, 28), "Aniversário", 120)
        # veio só a data nova: tipo e convidados ficam
        assert ev.gravar(c, 34, lid, {"data": "2026-11-29"})
        assert _le(c, lid) == (date(2026, 11, 29), "Aniversário", 120)
        # nada válido: não toca em nada (e diz que não gravou)
        assert not ev.gravar(c, 34, lid, {"data": "AAAA-MM-DD", "tipo": "", "convidados": 0})
        assert _le(c, lid) == (date(2026, 11, 29), "Aniversário", 120)


def test_so_vazios_e_o_modo_do_agente_nao_passa_por_cima_do_vendedor(pool):
    with pool.connection() as c:
        lid = _lead(c, evento_em=date(2027, 1, 16), evento_tipo="Formatura")
        assert ev.gravar(c, 34, lid, {"data": "2027-02-01", "tipo": "casamento", "convidados": 80},
                         so_vazios=True)
        # data e tipo eram do vendedor: ficam. Convidados estava vazio: entra.
        assert _le(c, lid) == (date(2027, 1, 16), "Formatura", 80)


def test_gravar_respeita_a_conta(pool):
    with pool.connection() as c:
        lid = _lead(c)
        ev.gravar(c, 99, lid, {"data": "2027-02-01"})
        assert _le(c, lid) == (None, None, None)


def test_sincronizar_do_orcamento_copia_pros_leads_amarrados(pool):
    with pool.connection() as c:
        oid = c.execute("insert into orcamentos (conta_id, evento) values (34, %s::jsonb) returning id",
                        ('{"data":"31/12/2026","tipo":"casamento","convidados":"50","inicio":"21h"}',)
                        ).fetchone()[0]
        a = _lead(c, orcamento_id=oid)
        b = _lead(c, orcamento_id=oid, evento_tipo="Bodas")
        solto = _lead(c)
        assert ev.sincronizar_do_orcamento(c, 34, oid) == 2
        assert _le(c, a) == (date(2026, 12, 31), "casamento", 50)
        # o orçamento é o documento formal: sobrescreve o tipo digitado
        assert _le(c, b) == (date(2026, 12, 31), "casamento", 50)
        assert _le(c, solto) == (None, None, None)
        assert ev.sincronizar_do_orcamento(c, 34, None) == 0


def test_gravar_numa_base_sem_a_migracao_nao_derruba_a_transacao(pool):
    """O agente e o salvamento da proposta chamam isto no meio da transação deles:
    um erro aqui não pode abortar o que vem depois (savepoint por dentro)."""
    with pool.connection() as c:
        c.execute("alter table prospeccao drop column evento_convidados")
        lid = _lead(c)
        assert ev.gravar(c, 34, lid, {"data": "2026-12-31"}) is False
        # a conexão continua usável na mesma transação
        assert c.execute("select 1").fetchone()[0] == 1


# ------------------------------------------------------------------ vista por mês
def test_colunas_por_mes_ordena_os_meses_e_poe_sem_data_por_ultimo():
    jan = _card(id=1, evento_em=date(2027, 1, 16), status="contatado")
    jan2 = _card(id=2, evento_em=date(2027, 1, 9), status="proposta")
    nov = _card(id=3, evento_em=date(2026, 11, 14), status="contatado")
    sem = _card(id=4, status="contatado")
    perdido = _card(id=5, evento_em=date(2026, 11, 20), status="perdido")
    cols, grupos = ev.colunas_por_mes([jan, jan2, nov, sem, perdido], AGORA)
    assert cols == [("2026-11", "Nov 26"), ("2027-01", "Jan 27"), ("sem", "Sem data")]
    assert [c["id"] for c in grupos["2027-01"][0]["cards"]] == [2, 1]     # pela data da festa
    assert grupos["2027-01"][0]["rotulo"] == "" and grupos["2027-01"][0]["n"] == 2
    assert [c["id"] for c in grupos["2026-11"][0]["cards"]] == [3]         # perdido fora
    assert grupos["sem"][0]["tipo"] == "entrada" and [c["id"] for c in grupos["sem"][0]["cards"]] == [4]


def test_colunas_por_mes_sem_lead_nenhum_so_tem_sem_data():
    assert ev.colunas_por_mes([], AGORA) == ([("sem", "Sem data")], {"sem": []})


def test_visita_curta_na_semana_e_fora_dela():
    agora = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)          # sexta
    amanha_13utc = datetime(2026, 9, 5, 13, 0, tzinfo=timezone.utc)    # sáb 10h em Brasília
    assert ev.visita_curta(amanha_13utc, agora) == "Visita sáb 10h"
    assert ev.visita_curta(datetime(2026, 9, 5, 13, 30, tzinfo=timezone.utc), agora) == "Visita sáb 10h30"
    assert ev.visita_curta(datetime(2026, 11, 21, 13, 0, tzinfo=timezone.utc), agora) == "Visita 21/11"
    assert ev.visita_curta(None, agora) == ""
