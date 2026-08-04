"""web/painel_agenda.py: card de compartilhar convites (_montar_share) — a
mensagem de convite passa a citar os envolvidos quando o evento tem 2+
convidados (feature "envolvidos no corpo da mensagem").

Roda com banco de TESTE separado (ver tests/conftest.py).
"""
import os
from datetime import timedelta
from pathlib import Path
from urllib.parse import unquote

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from finance import agenda as ag
from finance import convites as cv


class _FakeRequest:
    """Só o suficiente pra _montar_share/_convite_url: precisam de base_url."""
    def __init__(self, base="http://testserver"):
        self.base_url = base


@pytest.fixture(scope="module")
def pool():
    p = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=4,
                       open=True, kwargs={"prepare_threshold": None})
    init_schema(p)
    migr = Path(__file__).resolve().parent.parent / "db" / "migracoes"
    with p.connection() as c:
        for nome in ("098_agenda.sql", "099_agenda_tipo.sql", "100_evento_convidados.sql"):
            c.execute((migr / nome).read_text(encoding="utf-8"))
        c.commit()
    yield p
    p.close()


@pytest.fixture()
def conta_id(pool):
    with pool.connection() as c:
        cid = c.execute(
            "insert into contas (tipo, nome) values ('pj','Padaria Central') returning id"
        ).fetchone()[0]
        c.commit()
    return cid


def test_montar_share_cita_envolvidos_com_dois_ou_mais(pool, conta_id):
    from web.painel_agenda import _montar_share
    ev = ag.criar_evento(pool, conta_id, "Reunião", ag.agora_brt() + timedelta(days=1),
                         local="Escritório")
    cv.criar_convidado(pool, conta_id, ev["id"], "Ana", "86999990000")
    cv.criar_convidado(pool, conta_id, ev["id"], "Carlos", "86999991111")
    share = _montar_share(_FakeRequest(), pool, conta_id, str(ev["id"]), "")
    assert share is not None and share["total"] == 2
    for g in share["guests"]:
        assert "Com: Ana e Carlos" in unquote(g["wa"])


def test_montar_share_sem_citar_com_um_so_convidado(pool, conta_id):
    from web.painel_agenda import _montar_share
    ev = ag.criar_evento(pool, conta_id, "Café", ag.agora_brt() + timedelta(days=1))
    cv.criar_convidado(pool, conta_id, ev["id"], "Ana", "86999990000")
    share = _montar_share(_FakeRequest(), pool, conta_id, str(ev["id"]), "")
    assert share is not None and share["total"] == 1
    assert "Com:" not in unquote(share["guests"][0]["wa"])
