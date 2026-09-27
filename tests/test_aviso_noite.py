"""Os avisos à equipe esperam o dia (finance/aviso_noite.py, revisão de 27/09/2026).

A reserva que vence às 3h libera a data NA HORA; o Telegram do dono espera as 8h. O
aviso da IA pra equipe (`chip_regra.notificar`) e o da disputa de data também. A
manhã solta cada aviso uma vez só.

Banco dedicado e descartável; aplica a 432.
"""
import os
from datetime import datetime
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from finance import agenda as ag
from finance import aviso_noite as an
from finance import chip_regra as cr
from finance import festa_rotinas as frt
from finance import lembretes
from finance import notificar as nt
from finance import visita_rotinas as vr

BRT = ag.BRT
MIG = Path(__file__).resolve().parent.parent / "db" / "migracoes"
CONTA = 34
MADRUGADA = datetime(2026, 9, 30, 3, 0, tzinfo=BRT)
MANHA = datetime(2026, 9, 30, 8, 2, tzinfo=BRT)


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_aviso_noite_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity "
                  "where datname=%s and pid <> pg_backend_pid()", (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=4, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute("""create table membros (id bigserial primary key, conta_id bigint, nome text,
                       email text, whatsapp text, ativo boolean default true)""")
        c.execute("""create table eventos_agenda (id bigserial primary key, conta_id bigint,
                       titulo text, inicio timestamptz, fim timestamptz, status text,
                       pre_reserva_ate timestamptz)""")
        c.execute((MIG / "432_revisao_motores_parte2.sql").read_text(encoding="utf-8"))
        c.commit()
    yield p
    p.close()


@pytest.fixture()
def noite(monkeypatch):
    """A janela de verdade (8h às 21h) — o conftest a abre o dia todo."""
    monkeypatch.setattr(an, "HORAS", (8, 21))


@pytest.fixture()
def saiu(monkeypatch):
    out = {"dono": [], "membro": [], "festa": []}
    monkeypatch.setattr(nt, "enviar_para_dono", lambda pool, conta, texto: out["dono"].append(texto) or True)
    monkeypatch.setattr(cr, "_enviar_aviso", lambda pool, conta, mid, m, t, corpo, url:
                        out["membro"].append((mid, t)))
    monkeypatch.setattr(vr, "avisar", lambda pool, conta, mid, t, corpo, url, origem="": out["festa"].append((mid, t)) or True)
    return out


def _membro(pool):
    with pool.connection() as c:
        m = c.execute("insert into membros (conta_id, nome, email) values (%s,'Jacqueline','j@x.com') "
                      "returning id", (CONTA,)).fetchone()[0]
        c.commit()
    return m


def test_dentro_e_fora_da_janela(noite):
    assert not an.dentro(MADRUGADA)
    assert an.dentro(MANHA)
    assert not an.dentro(datetime(2026, 9, 30, 21, 0, tzinfo=BRT))


def test_a_reserva_vence_de_madrugada_a_data_libera_e_o_dono_sabe_de_manha(pool, noite, saiu, monkeypatch):
    with pool.connection() as c:
        ev = c.execute("""insert into eventos_agenda (conta_id, titulo, inicio, status, pre_reserva_ate)
                          values (%s,'15 anos',%s,'pre_reservado',%s) returning id""",
                       (CONTA, datetime(2027, 3, 13, 20, tzinfo=BRT),
                        MADRUGADA.replace(hour=2))).fetchone()[0]
        c.commit()
    monkeypatch.setattr(an, "dentro", lambda agora=None: an.HORAS[0] <= (agora or MADRUGADA).astimezone(BRT).hour < an.HORAS[1])
    assert lembretes._expirar_pre_reservas(pool, MADRUGADA) == 1
    with pool.connection() as c:
        assert c.execute("select status from eventos_agenda where id=%s", (ev,)).fetchone()[0] == "cancelado"
    assert saiu["dono"] == []                                     # a data já está livre
    assert an.soltar(pool, MADRUGADA) == 0
    assert an.soltar(pool, MANHA) == 1
    assert len(saiu["dono"]) == 1 and "15 anos" in saiu["dono"][0]
    assert an.soltar(pool, MANHA) == 0                           # uma vez só


def test_o_aviso_da_ia_pra_equipe_espera_a_manha(pool, noite, saiu, monkeypatch):
    m = _membro(pool)
    monkeypatch.setattr(an, "dentro", lambda agora=None: an.HORAS[0] <= (agora or MADRUGADA).astimezone(BRT).hour < an.HORAS[1])
    assert cr.notificar(pool, CONTA, m, "📝 Orçamento da IA pra conferir", "Ana pediu", "/x") is True
    assert saiu["membro"] == []
    assert an.soltar(pool, MANHA) == 1
    assert saiu["membro"] == [(m, "📝 Orçamento da IA pra conferir")]


def test_a_disputa_de_data_de_madrugada_avisa_de_manha(pool, noite, saiu, monkeypatch):
    m = _membro(pool)
    monkeypatch.setattr(an, "dentro", lambda agora=None: an.HORAS[0] <= (agora or MADRUGADA).astimezone(BRT).hour < an.HORAS[1])
    assert frt._avisar(pool, CONTA, m, "⚠️ Outro cliente pediu sábado 13/02", "…", "/cockpit/lead/1")
    assert saiu["festa"] == []
    an.soltar(pool, MANHA)
    assert saiu["festa"] == [(m, "⚠️ Outro cliente pediu sábado 13/02")]


def test_de_dia_sai_na_hora(pool, saiu):
    m = _membro(pool)
    cr.notificar(pool, CONTA, m, "Visita marcada pela IA", "…", "/x")
    assert saiu["membro"] == [(m, "Visita marcada pela IA")]
    with pool.connection() as c:
        assert c.execute("select count(*) from avisos_adiados").fetchone()[0] == 0


def test_sem_a_tabela_o_aviso_sai_na_hora(pool, noite, saiu, monkeypatch):
    m = _membro(pool)
    with pool.connection() as c:
        c.execute("drop table avisos_adiados")
        c.commit()
    monkeypatch.setattr(an, "dentro", lambda agora=None: False)
    cr.notificar(pool, CONTA, m, "Comprovante do sinal chegou", "…", "/x")
    assert saiu["membro"] == [(m, "Comprovante do sinal chegou")]


def test_a_migracao_432_e_idempotente(pool):
    with pool.connection() as c:
        c.execute((MIG / "432_revisao_motores_parte2.sql").read_text(encoding="utf-8"))
        c.commit()
