"""A trava de ciclo do poller (db/trava.py, migração 610).

O CASO (03/10/2026, Prime, conta 34). A trava do resgate era `pg_try_advisory_lock`, de
SESSÃO. Atrás do pooler do Supabase ela era pega numa conexão de servidor e o unlock caía
em outra ("you don't own a lock", em todo ciclo): os dois workers rodaram o ciclo juntos
e um cliente recebeu a mensagem que a IA tinha decidido não mandar. Aqui a trava é uma
linha, e cada passo abre e fecha a própria conexão — como o pooler faz.
"""
import os
import threading
from datetime import timedelta
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from db import trava

BASE = Path(__file__).resolve().parents[1] / "db" / "migracoes"


def _pool(dbname, com_610=True):
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    with admin.connection() as c:
        c.autocommit = True
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity "
                  "where datname=%s and pid <> pg_backend_pid()", (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=6, open=True, kwargs={"prepare_threshold": None})
    if com_610:
        with p.connection() as c:
            c.execute((BASE / "610_travas.sql").read_text(encoding="utf-8"))
            c.commit()
    return p


@pytest.fixture()
def pool():
    p = _pool("zaq_trava_test")
    yield p
    p.close()


def test_so_um_leva_e_o_outro_nao_apaga(pool):
    assert trava.pegar(pool, "resgate", "a") is True
    assert trava.pegar(pool, "resgate", "b") is False
    trava.soltar(pool, "resgate", "b")                 # não é dele: nada muda
    assert trava.pegar(pool, "resgate", "b") is False
    trava.soltar(pool, "resgate", "a")
    assert trava.pegar(pool, "resgate", "b") is True


def test_nomes_diferentes_nao_se_atrapalham(pool):
    assert trava.pegar(pool, "resgate", "a") is True
    assert trava.pegar(pool, "ia_insiste", "a") is True


def test_o_prazo_vencido_libera_e_o_dono_antigo_nao_solta_o_novo(pool):
    assert trava.pegar(pool, "resgate", "a", timedelta(seconds=-1)) is True   # já nasce vencida
    assert trava.pegar(pool, "resgate", "b") is True
    trava.soltar(pool, "resgate", "a")                 # o que morreu acorda tarde: não solta a de b
    assert trava.pegar(pool, "resgate", "c") is False


def test_dois_ciclos_ao_mesmo_tempo_so_um_roda(pool):
    """Os dois workers do uvicorn chegam juntos: um roda, o outro passa a vez."""
    rodou, dentro, largada = [], threading.Event(), threading.Barrier(2)

    def worker(nome):
        largada.wait()
        with trava.ciclo(pool, "resgate", 771180) as pegou:
            if pegou:
                rodou.append(nome)
                dentro.wait(2)                         # segura enquanto o outro tenta

    ts = [threading.Thread(target=worker, args=(n,)) for n in ("w1", "w2")]
    for t in ts:
        t.start()
    for t in ts:
        t.join(5)
    dentro.set()
    assert len(rodou) == 1
    with pool.connection() as c:
        assert c.execute("select count(*) from travas").fetchone()[0] == 0


def test_o_ciclo_solta_mesmo_quando_da_erro(pool):
    with pytest.raises(RuntimeError):
        with trava.ciclo(pool, "resgate", 771180) as pegou:
            assert pegou
            raise RuntimeError("a IA caiu")
    with trava.ciclo(pool, "resgate", 771180) as pegou:
        assert pegou


def test_banco_sem_a_610_usa_a_trava_antiga():
    p = _pool("zaq_trava_sem_610", com_610=False)
    try:
        assert trava.pegar(p, "resgate", "a") is None
        with trava.ciclo(p, "resgate", 771180) as pegou:
            assert pegou
            with p.connection() as c:
                assert c.execute("select count(*) from pg_locks where locktype='advisory' "
                                 "and objid=771180").fetchone()[0] == 1
        with p.connection() as c:
            assert c.execute("select count(*) from pg_locks where locktype='advisory' "
                             "and objid=771180").fetchone()[0] == 0
    finally:
        p.close()
