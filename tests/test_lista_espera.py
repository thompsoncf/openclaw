"""A lista de espera por data (finance/lista_espera): quem quer um dia que a
empresa já vendeu, e o aviso no instante em que esse dia abre.

O cenário é a Prime em miniatura, com `festas_por_dia = 1` — a régua que o dono
aprovou em 06/09: em 29 dias de agenda ela nunca teve duas festas no mesmo dia.
Uma segunda conta, sem o número, prova o portão: nada acontece pra ela.

Banco dedicado e descartável; aplica a 216.
"""
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from psycopg_pool import ConnectionPool

from finance import lista_espera as le

BRT = ZoneInfo("America/Sao_Paulo")
MIG = Path(__file__).resolve().parent.parent / "db" / "migracoes"
HOJE = date(2026, 9, 6)

_SQL = """
create table nichos (id bigserial primary key, nome text, slug text unique, ativo boolean default true);
create table contas (id bigserial primary key, nome text, nicho_id bigint references nichos(id),
  criado_em timestamptz not null default now());
create table membros (id bigserial primary key, conta_id bigint, nome text,
  papel text default 'vendedor', ativo boolean default true);
create table prospeccao (id bigserial primary key, conta_id bigint, vendedor_id bigint,
  empresa text, contato text, status text default 'novo', evento_em date, evento_tipo text,
  perda_motivo text, orcamento_id bigint,
  criado_em timestamptz default now(), atualizado_em timestamptz default now());
create table eventos_agenda (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  titulo text, inicio timestamptz, status text default 'ativo', desfecho text,
  tipo text default 'empresa', tipo_evento text);
"""


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_lista_espera_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        c.execute((MIG / "216_lista_espera_data.sql").read_text(encoding="utf-8"))
        c.execute("insert into nichos (nome, slug) values ('Eventos','eventos')")
        c.commit()
    yield p
    p.close()


def _conta(pool, nome, festas=1, nicho="eventos"):
    with pool.connection() as c:
        cid = c.execute("""insert into contas (nome, festas_por_dia, nicho_id)
                           values (%s, %s, (select id from nichos where slug=%s)) returning id""",
                        (nome, festas, nicho)).fetchone()[0]
        c.commit()
    return cid


def _vend(pool, conta, nome="Jacqueline"):
    with pool.connection() as c:
        v = c.execute("insert into membros (conta_id, nome) values (%s,%s) returning id",
                      (conta, nome)).fetchone()[0]
        c.commit()
    return v


def _lead(pool, conta, vend, nome, dia, *, status="qualificado", tipo="Casamento", motivo=None):
    with pool.connection() as c:
        lid = c.execute("""insert into prospeccao (conta_id, vendedor_id, contato, status, evento_em, evento_tipo, perda_motivo)
                           values (%s,%s,%s,%s,%s,%s,%s) returning id""",
                        (conta, vend, nome, status, dia, tipo, motivo)).fetchone()[0]
        c.commit()
    return lid


def _festa(pool, conta, dia, tipo="Locação", status="ativo"):
    with pool.connection() as c:
        eid = c.execute("""insert into eventos_agenda (conta_id, titulo, inicio, tipo, tipo_evento, status)
                           values (%s,'Festa',%s,'empresa',%s,%s) returning id""",
                        (conta, datetime.combine(dia, datetime.min.time(), tzinfo=BRT).replace(hour=18),
                         tipo, status)).fetchone()[0]
        c.commit()
    return eid


def _cancelar(pool, evento_id):
    with pool.connection() as c:
        c.execute("update eventos_agenda set status='cancelado' where id=%s", (evento_id,))
        c.commit()


# ------------------------------------------------------------------ a régua

def test_data_tomada_conta_so_festa_e_respeita_o_limite_da_conta(pool):
    conta = _conta(pool, "Prime", festas=1)
    dia = date(2026, 10, 10)
    assert le.data_tomada(pool, conta, dia)["tomada"] is False
    # visita não toma o salão: só festa (tipo_evento preenchido)
    with pool.connection() as c:
        c.execute("""insert into eventos_agenda (conta_id, titulo, inicio, tipo, tipo_evento)
                     values (%s,'Visita',%s,'empresa',null)""",
                  (conta, datetime(2026, 10, 10, 15, tzinfo=BRT)))
        c.commit()
    assert le.data_tomada(pool, conta, dia)["tomada"] is False
    _festa(pool, conta, dia, tipo="Casamento")
    st = le.data_tomada(pool, conta, dia)
    assert st["tomada"] is True and st["festas"] == 1 and st["limite"] == 1 and st["o_que"] == "Casamento"
    # a mesma agenda numa conta que faz duas por dia: ainda tem vaga
    dois = _conta(pool, "Dois salões", festas=2)
    _festa(pool, dois, dia)
    assert le.data_tomada(pool, dois, dia)["tomada"] is False
    _festa(pool, dois, dia)
    assert le.data_tomada(pool, dois, dia)["tomada"] is True


def test_festa_cancelada_nao_toma_a_data(pool):
    conta = _conta(pool, "Cancela")
    dia = date(2027, 1, 16)
    ev = _festa(pool, conta, dia)
    assert le.data_tomada(pool, conta, dia)["tomada"] is True
    _cancelar(pool, ev)
    assert le.data_tomada(pool, conta, dia)["tomada"] is False


def test_pre_reserva_toma_a_data_ate_vencer(pool):
    """Data segurada é data ocupada: senão o dono vende o mesmo sábado duas vezes."""
    conta = _conta(pool, "Pre")
    dia = date(2026, 11, 21)
    _festa(pool, conta, dia, status="pre_reservado")
    assert le.data_tomada(pool, conta, dia)["tomada"] is True


def test_conta_sem_o_numero_nao_usa_lista(pool):
    conta = _conta(pool, "Sem número", festas=None)
    assert le.festas_por_dia(pool, conta) is None
    assert le.data_tomada(pool, conta, date(2026, 10, 10)) is None
    assert le.datas_livres_perto(pool, conta, date(2026, 10, 10)) == []
    assert le.sincronizar(pool, conta, HOJE) == {"entraram": 0, "sairam": 0}
    assert le.por_data(pool, conta) == [] and le.datas_que_abriram(pool, conta) == []


def test_conta_que_nao_vende_festa_nao_usa_lista_nem_com_o_numero(pool):
    """Regra 6: o número existe, mas o nicho manda. Consultoria não tem data."""
    with pool.connection() as c:
        c.execute("insert into nichos (nome, slug) values ('Consultoria','consultoria') on conflict do nothing")
        c.commit()
    conta = _conta(pool, "Consultoria", festas=1, nicho="consultoria")
    assert le.festas_por_dia(pool, conta) == 1
    assert le.usa_lista(pool, conta) is False


# ------------------------------------------------------------------ datas livres

def test_datas_livres_perto_preferem_o_mesmo_dia_da_semana_e_nunca_o_passado(pool):
    conta = _conta(pool, "Livres")
    sab = date(2026, 10, 10)          # sábado
    for d in (sab, date(2026, 10, 17), date(2026, 10, 3)):
        _festa(pool, conta, d)
    livres = le.datas_livres_perto(pool, conta, sab, hoje=HOJE)
    assert [x["data"] for x in livres] == [date(2026, 10, 24), date(2026, 9, 26), date(2026, 10, 31)]
    assert all(x["mesmo_dia_semana"] for x in livres)      # sábado pede sábado
    # data no passado nunca é sugerida
    passado = date(2026, 8, 1)
    assert all(x["data"] >= HOJE for x in le.datas_livres_perto(pool, conta, passado, hoje=HOJE))


# ------------------------------------------------------------------ entrar, sair, sincronizar

def test_sincronizar_poe_quem_pede_data_tomada_e_tira_quem_saiu(pool):
    conta = _conta(pool, "Sync")
    v = _vend(pool, conta)
    dia = date(2026, 12, 12)
    _festa(pool, conta, dia)
    tomada = _lead(pool, conta, v, "Quer 12/12", dia)
    livre = _lead(pool, conta, v, "Quer 19/12", date(2026, 12, 19))
    ganho = _lead(pool, conta, v, "Fechou", dia, status="ganho")
    r = le.sincronizar(pool, conta, HOJE)
    assert r["entraram"] == 1 and r["sairam"] == 0
    assert le.esperando_por(pool, conta, tomada) == [dia]
    assert le.esperando_por(pool, conta, livre) == [] and le.esperando_por(pool, conta, ganho) == []
    # rodar de novo não duplica
    assert le.sincronizar(pool, conta, HOJE)["entraram"] == 0
    # o lead fecha: sai como "fechou"
    with pool.connection() as c:
        c.execute("update prospeccao set status='ganho' where id=%s", (tomada,)); c.commit()
    assert le.sincronizar(pool, conta, HOJE)["sairam"] == 1
    assert le.esperando_por(pool, conta, tomada) == []
    with pool.connection() as c:
        m = c.execute("select saiu_motivo from lista_espera_data where prospeccao_id=%s", (tomada,)).fetchone()[0]
    assert m == "fechou"


def test_lead_que_muda_de_data_sai_da_lista_da_data_antiga(pool):
    conta = _conta(pool, "Mudou")
    v = _vend(pool, conta)
    velha, nova = date(2026, 11, 14), date(2026, 11, 28)
    _festa(pool, conta, velha)
    lid = _lead(pool, conta, v, "Mudou de ideia", velha)
    le.sincronizar(pool, conta, HOJE)
    assert le.esperando_por(pool, conta, lid) == [velha]
    with pool.connection() as c:
        c.execute("update prospeccao set evento_em=%s where id=%s", (nova, lid)); c.commit()
    le.sincronizar(pool, conta, HOJE)
    assert le.esperando_por(pool, conta, lid) == []
    with pool.connection() as c:
        assert c.execute("select saiu_motivo from lista_espera_data where prospeccao_id=%s",
                         (lid,)).fetchone()[0] == "mudou_data"


def test_perdido_por_data_indisponivel_continua_esperando(pool):
    """Ele ainda quer aquele dia — é justamente quem avisar quando abrir."""
    conta = _conta(pool, "Perdido")
    v = _vend(pool, conta)
    dia = date(2026, 10, 31)
    _festa(pool, conta, dia)
    quer = _lead(pool, conta, v, "Ainda quer", dia, status="perdido", motivo="data_indisponivel")
    foi = _lead(pool, conta, v, "Foi embora", dia, status="perdido", motivo="achou_caro")
    le.sincronizar(pool, conta, HOJE)
    assert le.esperando_por(pool, conta, quer) == [dia]
    assert le.esperando_por(pool, conta, foi) == []


def test_sair_e_voltar_reaproveita_a_linha(pool):
    conta = _conta(pool, "Volta")
    v = _vend(pool, conta)
    dia = date(2027, 2, 20)
    _festa(pool, conta, dia)
    lid = _lead(pool, conta, v, "Vai e volta", dia)
    assert le.entrar(pool, conta, lid, dia) is True
    assert le.sair(pool, conta, lid, "desistiu") == 1
    assert le.esperando_por(pool, conta, lid) == []
    assert le.entrar(pool, conta, lid, dia) is True
    assert le.esperando_por(pool, conta, lid) == [dia]
    with pool.connection() as c:
        n = c.execute("select count(*) from lista_espera_data where prospeccao_id=%s", (lid,)).fetchone()[0]
    assert n == 1          # uma linha só: histórico não vira duplicata


# ------------------------------------------------------------------ a lista pro painel

def test_por_data_agrupa_e_poe_o_que_abriu_na_frente(pool):
    conta = _conta(pool, "Painel")
    v1, v2 = _vend(pool, conta, "Jacqueline"), _vend(pool, conta, "Pedro")
    cheia, abriu = date(2026, 10, 10), date(2027, 1, 16)
    _festa(pool, conta, cheia)
    ev = _festa(pool, conta, abriu)
    a = _lead(pool, conta, v1, "Renata", cheia)
    b = _lead(pool, conta, v2, "Vitor", cheia, tipo="Aniversário")
    c_ = _lead(pool, conta, v1, "Marcos", abriu, tipo="Formatura")
    le.sincronizar(pool, conta, HOJE)
    _cancelar(pool, ev)                     # a data de 16/01 abre
    lista = le.por_data(pool, conta, HOJE)
    assert [d["data"] for d in lista] == [abriu, cheia]     # o que abriu vem primeiro
    d0, d1 = lista
    assert d0["abriu"] is True and d0["n"] == 1 and d0["quem"][0]["nome"] == "Marcos"
    assert d1["abriu"] is False and d1["n"] == 2
    assert {q["nome"] for q in d1["quem"]} == {"Renata", "Vitor"}
    assert {q["vendedor"] for q in d1["quem"]} == {"Jacqueline", "Pedro"}
    assert d1["livres"] and all(x["data"] != cheia for x in d1["livres"])
    assert d0["livres"] == []               # já abriu: não precisa de alternativa


# ------------------------------------------------------------------ a data abriu

def _fake_avisos():
    pushes, telegramas = [], []

    def push(conta, membro, titulo, corpo):
        pushes.append((conta, membro, titulo, corpo)); return 1

    def telegram(conta, texto):
        telegramas.append((conta, texto)); return True
    return pushes, telegramas, push, telegram


def test_quando_a_data_abre_cada_vendedor_e_avisado_e_o_dono_recebe_o_resumo(pool):
    conta = _conta(pool, "Abriu")
    v1, v2 = _vend(pool, conta, "Jacqueline"), _vend(pool, conta, "Pedro")
    dia = date(2027, 1, 23)
    ev = _festa(pool, conta, dia)
    a = _lead(pool, conta, v1, "Marcos", dia, tipo="Formatura")
    b = _lead(pool, conta, v2, "Luana", dia)
    le.sincronizar(pool, conta, HOJE)
    assert le.datas_que_abriram(pool, conta, HOJE) == []      # ainda tomada
    _cancelar(pool, ev)
    abertas = le.datas_que_abriram(pool, conta, HOJE)
    assert {x["nome"] for x in abertas} == {"Marcos", "Luana"}
    pushes, telegramas, push, telegram = _fake_avisos()
    assert le.avisar(pool, conta, HOJE, push=push, telegram=telegram) == 2
    assert {p[1] for p in pushes} == {v1, v2}                 # cada um pro SEU vendedor
    assert all("23/01 abriu" in p[2] for p in pushes)
    assert any("Formatura" in p[3] for p in pushes)
    assert len(telegramas) == 1 and "Marcos" in telegramas[0][1] and "Luana" in telegramas[0][1]
    # não avisa duas vezes
    p2, t2, push2, tel2 = _fake_avisos()
    assert le.avisar(pool, conta, HOJE, push=push2, telegram=tel2) == 0
    assert p2 == [] and t2 == []


def test_a_pre_reserva_que_vence_abre_a_data_pelo_mesmo_caminho(pool):
    """Não há três gatilhos: a pergunta é sempre "hoje tem vaga?"."""
    conta = _conta(pool, "Venceu")
    v = _vend(pool, conta)
    dia = date(2027, 3, 13)
    ev = _festa(pool, conta, dia, status="pre_reservado")
    _lead(pool, conta, v, "Esperando", dia)
    le.sincronizar(pool, conta, HOJE)
    assert le.datas_que_abriram(pool, conta, HOJE) == []
    with pool.connection() as c:                  # é o que expirar_pre_reservas faz
        c.execute("update eventos_agenda set status='cancelado' where id=%s", (ev,)); c.commit()
    assert len(le.datas_que_abriram(pool, conta, HOJE)) == 1


def test_data_no_passado_nao_avisa_ninguem(pool):
    conta = _conta(pool, "Passado")
    v = _vend(pool, conta)
    dia = date(2026, 8, 1)
    lid = _lead(pool, conta, v, "Antigo", dia)
    le.entrar(pool, conta, lid, dia)
    assert le.datas_que_abriram(pool, conta, HOJE) == []
    assert le.por_data(pool, conta, HOJE) == []


def test_rodar_cobre_so_as_contas_que_usam_a_lista(pool):
    usa = _conta(pool, "Usa", festas=1)
    nao = _conta(pool, "Não usa", festas=None)
    v = _vend(pool, usa)
    v2 = _vend(pool, nao)
    dia = date(2027, 4, 10)
    ev = _festa(pool, usa, dia)
    _festa(pool, nao, dia)
    _lead(pool, usa, v, "Da Prime", dia)
    _lead(pool, nao, v2, "Da outra", dia)
    r = le.rodar(pool, HOJE)
    assert r["entraram"] >= 1
    with pool.connection() as c:
        assert c.execute("select count(*) from lista_espera_data where conta_id=%s", (nao,)).fetchone()[0] == 0
    _cancelar(pool, ev)
    pushes, telegramas, push, telegram = _fake_avisos()
    # rodar usa os caminhos reais; aqui basta que datas_que_abriram enxergue
    assert any(x["nome"] == "Da Prime" for x in le.datas_que_abriram(pool, usa, HOJE))


def test_migracao_216_e_idempotente(pool):
    with pool.connection() as c:
        c.execute((MIG / "216_lista_espera_data.sql").read_text(encoding="utf-8"))
        c.commit()
