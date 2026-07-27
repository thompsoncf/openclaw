"""Agenda própria do Zaq: parsing de data, links (Google/.ics) e CRUD + tools.

Roda com banco de TESTE separado (ver tests/conftest.py).
"""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from finance import agenda as ag
from finance.agenda_tools import construir_ferramentas_agenda

BRT = timezone(timedelta(hours=-3))


# ---------- parsing de data/hora (puro) ----------

def test_parse_data_hora_completa():
    dt = ag.parse_datahora("28/07/2026 15:00")
    assert (dt.year, dt.month, dt.day, dt.hour, dt.minute) == (2026, 7, 28, 15, 0)
    assert dt.utcoffset() == timedelta(hours=-3)      # Brasília


def test_parse_iso_e_sem_hora():
    assert ag.parse_datahora("2026-07-28 09:30").hour == 9
    d = ag.parse_datahora("25/12/2026")               # sem hora -> 09:00
    assert (d.month, d.day, d.hour) == (12, 25, 9)


def test_parse_invalido_volta_none():
    assert ag.parse_datahora("qualquer coisa") is None
    assert ag.parse_datahora("") is None


# ---------- links de "adicionar ao calendário" (puro) ----------

def _ev(**kw):
    base = {"id": 1, "titulo": "Reunião", "inicio": datetime(2026, 7, 28, 15, 0, tzinfo=BRT),
            "fim": None, "local": None, "descricao": None, "criado_em": None}
    base.update(kw)
    return base


def test_link_google_converte_pra_utc():
    url = ag.link_google(_ev())
    # 15:00 BRT = 18:00 UTC; fim default +1h = 19:00 UTC
    assert "dates=20260728T180000Z/20260728T190000Z" in url
    assert url.startswith("https://calendar.google.com/calendar/render?")
    assert "text=Reuni" in url


def test_ics_valido_e_escapa():
    ics = ag.feed_ics([_ev(titulo="Almoço; com, cliente", local="Café")])
    assert ics.startswith("BEGIN:VCALENDAR")
    assert "BEGIN:VEVENT" in ics and "END:VCALENDAR" in ics
    assert "SUMMARY:Almoço\\; com\\, cliente" in ics       # ; e , escapados
    assert "LOCATION:Café" in ics


# ---------- CRUD + ferramentas (banco) ----------

@pytest.fixture(scope="module")
def pool():
    p = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=4,
                       open=True, kwargs={"prepare_threshold": None})
    init_schema(p)
    ddl = (Path(__file__).resolve().parent.parent / "db" / "migracoes" / "098_agenda.sql")
    with p.connection() as c:
        c.execute(ddl.read_text(encoding="utf-8"))
        c.commit()
    yield p
    p.close()


@pytest.fixture()
def conta_id(pool):
    with pool.connection() as c:
        cid = c.execute("insert into contas (tipo, nome) values ('pf','Teste Agenda') returning id").fetchone()[0]
        c.commit()
    return cid


def test_criar_listar_cancelar(pool, conta_id):
    amanha = ag.agora_brt().replace(hour=10, minute=0, second=0, microsecond=0) + timedelta(days=1)
    ev = ag.criar_evento(pool, conta_id, "Dentista", amanha, local="Clínica X")
    assert ev["id"] and ev["titulo"] == "Dentista"
    prox = ag.proximos(pool, conta_id)
    assert any(e["id"] == ev["id"] for e in prox)
    assert ag.cancelar_evento(pool, conta_id, ev["id"]) is True
    assert all(e["id"] != ev["id"] for e in ag.proximos(pool, conta_id))   # sumiu


def test_isolamento_por_conta(pool, conta_id):
    outra = None
    with pool.connection() as c:
        outra = c.execute("insert into contas (tipo,nome) values ('pf','Outra') returning id").fetchone()[0]
        c.commit()
    ag.criar_evento(pool, conta_id, "So minha", ag.agora_brt() + timedelta(days=1))
    assert ag.proximos(pool, outra) == []          # a outra conta não vê


def test_ferramentas_marcar_ver_cancelar(pool, conta_id):
    ferrs = {f.nome: f for f in construir_ferramentas_agenda(pool, conta_id, membro_id=None)}
    r = ferrs["marcar_evento"].executar({"titulo": "Reunião contador", "inicio": "28/07/2099 15:00"})
    assert "Marquei" in r and "calendar.google.com" in r     # confirma + link
    v = ferrs["ver_agenda"].executar({"periodo": ""})
    assert "Reunião contador" in v
    c = ferrs["cancelar_evento"].executar({"titulo": "Reunião contador"})
    assert "Cancelei" in c


def test_ferramenta_marcar_pede_data_quando_nao_entende(pool, conta_id):
    ferrs = {f.nome: f for f in construir_ferramentas_agenda(pool, conta_id)}
    r = ferrs["marcar_evento"].executar({"titulo": "X", "inicio": "sei la quando"})
    assert "data" in r.lower() or "quando" in r.lower()      # pede a data, não quebra
