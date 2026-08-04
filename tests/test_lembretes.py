"""Motor de lembretes da agenda (resumo do dia + aviso antes).

Não manda Telegram de verdade: monkeypatcha notificar.enviar_para_dono pra capturar
o que sairia. Roda com banco de TESTE separado (ver tests/conftest.py).
"""
import os
from datetime import timedelta
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from finance import agenda as ag
from finance import lembretes as lb
from finance import notificar


@pytest.fixture(scope="module")
def pool():
    p = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=4,
                       open=True, kwargs={"prepare_threshold": None})
    init_schema(p)
    migr = Path(__file__).resolve().parent.parent / "db" / "migracoes"
    with p.connection() as c:
        for nome in ("098_agenda.sql", "099_agenda_tipo.sql", "100_evento_convidados.sql",
                    "101_agenda_lembretes.sql", "126_agenda_avisar_convidados.sql"):
            c.execute((migr / nome).read_text(encoding="utf-8"))
        c.commit()
    yield p
    p.close()


@pytest.fixture(autouse=True)
def _isola(pool):
    """rodar() varre TODAS as contas (certo em produção). No teste, limpa as tabelas
    da agenda antes de cada caso pra config/eventos de um teste não vazarem pro outro."""
    with pool.connection() as c:
        c.execute("truncate table lembretes_enviados, agenda_config, eventos_agenda "
                  "restart identity cascade")
        c.commit()
    yield


@pytest.fixture()
def conta_id(pool):
    with pool.connection() as c:
        cid = c.execute("insert into contas (tipo, nome) values ('pf','Teste Lembrete') returning id").fetchone()[0]
        c.commit()
    return cid


@pytest.fixture()
def enviados(monkeypatch):
    """Captura o que sairia pro dono (em vez de mandar Telegram)."""
    saidas = []
    monkeypatch.setattr(notificar, "enviar_para_dono",
                        lambda pool, conta_id, texto: (saidas.append((conta_id, texto)) or True))
    return saidas


def test_resumo_do_dia_manda_uma_vez(pool, conta_id, enviados):
    agora = ag.agora_brt().replace(hour=7, minute=3, second=0, microsecond=0)
    # 2 eventos hoje
    ag.criar_evento(pool, conta_id, "Dentista", agora.replace(hour=10))
    ag.criar_evento(pool, conta_id, "Reunião", agora.replace(hour=15), local="Online")
    ag.salvar_config(pool, conta_id, resumo_ativo=True, hora_resumo=7, aviso_antes_min=None)
    r = lb.rodar(pool, agora=agora)
    assert r["resumo"] == 1 and len(enviados) == 1
    txt = enviados[0][1]
    assert "agenda de hoje" in txt and "Dentista" in txt and "Reunião" in txt
    # roda de novo na mesma hora -> NÃO repete (dedup por dia)
    lb.rodar(pool, agora=agora.replace(minute=40))
    assert len(enviados) == 1


def test_resumo_nao_manda_fora_da_hora_nem_sem_evento(pool, conta_id, enviados):
    agora = ag.agora_brt().replace(hour=7, minute=0, second=0, microsecond=0)
    ag.salvar_config(pool, conta_id, resumo_ativo=True, hora_resumo=7, aviso_antes_min=None)
    # sem eventos hoje -> nada
    assert lb.rodar(pool, agora=agora)["resumo"] == 0 and enviados == []
    # com evento, mas fora da hora do resumo -> nada
    ag.criar_evento(pool, conta_id, "Almoço", agora.replace(hour=12))
    assert lb.rodar(pool, agora=agora.replace(hour=9))["resumo"] == 0 and enviados == []


def test_aviso_antes_dispara_na_janela_uma_vez(pool, conta_id, enviados):
    agora = ag.agora_brt().replace(hour=14, minute=0, second=0, microsecond=0)
    ag.criar_evento(pool, conta_id, "Consulta", agora + timedelta(minutes=20), local="Clínica")
    ag.salvar_config(pool, conta_id, resumo_ativo=False, hora_resumo=7, aviso_antes_min=30)
    r = lb.rodar(pool, agora=agora)                 # evento a 20 min, janela 30 -> avisa
    assert r["aviso"] == 1 and len(enviados) == 1
    assert "Consulta" in enviados[0][1] and "min" in enviados[0][1]
    lb.rodar(pool, agora=agora.replace(minute=5))   # de novo -> dedup por evento
    assert len(enviados) == 1


def test_aviso_nao_dispara_fora_da_janela(pool, conta_id, enviados):
    agora = ag.agora_brt().replace(hour=14, minute=0, second=0, microsecond=0)
    ag.criar_evento(pool, conta_id, "Longe", agora + timedelta(minutes=90))
    ag.salvar_config(pool, conta_id, resumo_ativo=False, hora_resumo=7, aviso_antes_min=30)
    assert lb.rodar(pool, agora=agora)["aviso"] == 0 and enviados == []


def test_so_aviso_ligado_nao_manda_resumo(pool, conta_id, enviados):
    agora = ag.agora_brt().replace(hour=7, minute=0, second=0, microsecond=0)
    ag.criar_evento(pool, conta_id, "Hoje cedo", agora.replace(hour=9))
    ag.salvar_config(pool, conta_id, resumo_ativo=False, hora_resumo=7, aviso_antes_min=15)
    # 7h, resumo desligado -> mesmo com evento hoje, não manda resumo
    assert lb.rodar(pool, agora=agora)["resumo"] == 0 and enviados == []
