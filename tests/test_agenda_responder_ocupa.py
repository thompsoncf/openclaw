"""RESPONDER "esse dia ocupa o espaço?" — o outro lado do "a conferir".

O #757 ensinou o calendário a admitir que não sabe. Aqui alguém responde.

ONDE A RESPOSTA MORA. Na caixa do dia, que já abre ao tocar no calendário — e
não numa tela nova. A pergunta é sobre UM DIA, e uma tela separada seria um
segundo lugar mostrando os mesmos compromissos, com duas chances de discordarem.

O QUE ESTE ARQUIVO PRENDE:
  • a coluna `ocupa_espaco` vence a derivação, e `None` devolve pra ela;
  • a lista `datas_a_conferir` só traz quem nenhum sinal alcança — e some
    sozinha quando alguém responde;
  • a rota não atravessa conta e não aceita resposta inventada;
  • a resposta NÃO toca em nada além dela mesma (título, data, status).

Banco dedicado e descartável, no padrão de tests/test_agenda_marcacao.py.
"""
from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool
from starlette.middleware.sessions import SessionMiddleware

from finance import agenda as ag
from web import painel_agenda as pa

CONTA = 9
OUTRA = 10
BASE = Path(__file__).resolve().parent.parent / "db" / "migracoes"


@pytest.fixture()
def cliente(monkeypatch):
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_agenda_ocupa"
    with admin.connection() as c:
        c.autocommit = True
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity where datname=%s",
                  (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    pool = ConnectionPool(url, min_size=1, max_size=3, open=True,
                          kwargs={"prepare_threshold": None})
    with pool.connection() as c:
        c.execute("create table contas (id bigserial primary key, tipo text, nome text, chip_de bigint)")
        c.execute("create table membros (id bigserial primary key, conta_id bigint, "
                  "nome text, papel text)")
        for nome in ("098_agenda.sql", "099_agenda_tipo.sql", "100_evento_convidados.sql",
                     "101_agenda_lembretes.sql", "126_agenda_avisar_convidados.sql",
                     "130_evento_desfecho.sql", "131_evento_link_online.sql",
                     "132_convidado_canal_resposta.sql", "139_agenda_mensagens_log.sql",
                     "146_agenda_enviar_confirmacao.sql", "160_agenda_pre_reserva.sql",
                     "163_evento_sinal_esperado.sql",
                     "179_agenda_tipo_e_hora_sugerida.sql",
                     "298_agenda_ocupa_espaco.sql"):
            c.execute((BASE / nome).read_text(encoding="utf-8"))
        c.execute("insert into contas (id, tipo, nome) values (%s,'pj','Espaço')", (CONTA,))
        c.execute("insert into contas (id, tipo, nome) values (%s,'pj','Vizinha')", (OUTRA,))
        c.commit()

    monkeypatch.setattr(pa, "get_pool", lambda: pool)
    monkeypatch.setattr(pa, "_acesso",
                        lambda request: ({"conta_id": CONTA, "membro_id": None,
                                          "papel": "dono"}, None))

    class _VendasFake:
        @staticmethod
        def vende_data(pool_, conta_id_):
            return True

        @staticmethod
        def fichas_de_eventos(pool_, conta_id_, ids):
            return {}

    monkeypatch.setattr(pa, "_vendas", lambda: _VendasFake)

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="teste-de-sessao")
    app.include_router(pa.router)
    c = TestClient(app, follow_redirects=False)
    c.pool = pool
    yield c
    pool.close()


def _duvidoso(pool, titulo="REUNIÃO COM ENGENHEIRA", conta_id=CONTA, dias=30):
    """Um compromisso que NENHUM sinal alcança — o caso real da Prime."""
    return ag.criar_evento(pool, conta_id, titulo,
                           ag.agora_brt() + timedelta(days=dias), tipo="empresa")


def _ocupa_no_banco(pool, eid):
    with pool.connection() as c:
        return c.execute("select ocupa_espaco from eventos_agenda where id=%s",
                         (eid,)).fetchone()[0]


# ------------------------------------------------------- a lista da pendência

def test_o_duvidoso_entra_na_lista(cliente):
    ev = _duvidoso(cliente.pool)
    assert [e["id"] for e in ag.datas_a_conferir(cliente.pool, CONTA)] == [ev["id"]]


def test_quem_o_sistema_sabe_classificar_nao_entra(cliente):
    """Visita, festa com tipo, pessoal e segurada já têm resposta — perguntar
    sobre elas seria pedir pro dono trabalhar de graça."""
    _duvidoso(cliente.pool, titulo="Visita — Andressa")
    ag.criar_evento(cliente.pool, CONTA, "Casamento — Eva",
                    ag.agora_brt() + timedelta(days=31), tipo="empresa",
                    tipo_evento="Casamento")
    ag.criar_evento(cliente.pool, CONTA, "Almoço em família",
                    ag.agora_brt() + timedelta(days=32), tipo="pessoal")
    ag.criar_evento(cliente.pool, CONTA, "Casamento — Denise",
                    ag.agora_brt() + timedelta(days=33), tipo="empresa",
                    pre_reserva_ate=ag.agora_brt() + timedelta(days=3))
    assert ag.datas_a_conferir(cliente.pool, CONTA) == []


def test_a_linha_some_sozinha_quando_alguem_responde(cliente):
    """Mesmo desenho de `horas_a_conferir`: a pendência não é arquivada, ela
    deixa de ser verdade."""
    ev = _duvidoso(cliente.pool)
    assert len(ag.datas_a_conferir(cliente.pool, CONTA)) == 1
    ag.responder_ocupa(cliente.pool, CONTA, ev["id"], True)
    assert ag.datas_a_conferir(cliente.pool, CONTA) == []


def test_responder_que_NAO_ocupa_tambem_tira_da_lista(cliente):
    """"Não ocupa" é resposta, não ausência de resposta."""
    ev = _duvidoso(cliente.pool)
    ag.responder_ocupa(cliente.pool, CONTA, ev["id"], False)
    assert ag.datas_a_conferir(cliente.pool, CONTA) == []
    assert ag.estado_da_data(
        ag.listar_eventos(cliente.pool, CONTA,
                          ag.agora_brt(), ag.agora_brt() + timedelta(days=60))[0]) == ag.LIVRE


def test_compromisso_que_ja_passou_nao_e_perguntado(cliente):
    """Ninguém vende ontem — e responder sobre o passado é trabalho sem venda."""
    _duvidoso(cliente.pool, dias=-5)
    assert ag.datas_a_conferir(cliente.pool, CONTA) == []


def test_entra_no_card_de_pendencias_e_soma_no_total(cliente):
    ev = _duvidoso(cliente.pool)
    p = ag.pendencias(cliente.pool, CONTA)
    assert [e["id"] for e in p["a_conferir"]] == [ev["id"]]
    assert p["total"] >= 1


# --------------------------------------------------------------- a gravação

def test_a_resposta_vence_a_derivacao_nos_dois_sentidos(cliente):
    ev = _duvidoso(cliente.pool, titulo="Visita — Ana")   # derivaria LIVRE
    ag.responder_ocupa(cliente.pool, CONTA, ev["id"], True)
    achado = ag.listar_eventos(cliente.pool, CONTA, ag.agora_brt(),
                               ag.agora_brt() + timedelta(days=60))[0]
    assert achado["ocupa_espaco"] is True
    assert ag.estado_da_data(achado) == ag.OCUPA


def test_limpar_devolve_o_compromisso_pra_derivacao(cliente):
    """O caminho de volta de quem clicou errado. Sem ele, um toque sem querer
    viraria um fato permanente sobre uma data."""
    ev = _duvidoso(cliente.pool)
    ag.responder_ocupa(cliente.pool, CONTA, ev["id"], True)
    ag.responder_ocupa(cliente.pool, CONTA, ev["id"], None)
    assert _ocupa_no_banco(cliente.pool, ev["id"]) is None
    assert len(ag.datas_a_conferir(cliente.pool, CONTA)) == 1


def test_a_resposta_nao_toca_em_mais_nada(cliente):
    """A escrita mais estreita que resolve a pergunta: título, data e status
    ficam como estavam. Regra 0 do CLAUDE.md — dado de cliente não se mexe de
    lado."""
    ev = _duvidoso(cliente.pool)
    with cliente.pool.connection() as c:
        antes = c.execute("select titulo, inicio, status, tipo, membro_id "
                          "from eventos_agenda where id=%s", (ev["id"],)).fetchone()
    ag.responder_ocupa(cliente.pool, CONTA, ev["id"], False)
    with cliente.pool.connection() as c:
        depois = c.execute("select titulo, inicio, status, tipo, membro_id "
                           "from eventos_agenda where id=%s", (ev["id"],)).fetchone()
    assert antes == depois


def test_nao_atravessa_conta(cliente):
    """A vizinha não responde pela casa."""
    ev = _duvidoso(cliente.pool, conta_id=OUTRA)
    assert ag.responder_ocupa(cliente.pool, CONTA, ev["id"], True) is False
    assert _ocupa_no_banco(cliente.pool, ev["id"]) is None


# ------------------------------------------------------------------- a rota

@pytest.mark.parametrize("resposta,esperado", [("ocupa", True), ("livre", False),
                                               ("limpar", None)])
def test_a_rota_grava_as_tres_respostas(cliente, resposta, esperado):
    ev = _duvidoso(cliente.pool)
    r = cliente.post("/painel/agenda/ocupa",
                     data={"evento_id": ev["id"], "resposta": resposta})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert _ocupa_no_banco(cliente.pool, ev["id"]) is esperado


def test_a_rota_recusa_resposta_inventada(cliente):
    ev = _duvidoso(cliente.pool)
    r = cliente.post("/painel/agenda/ocupa",
                     data={"evento_id": ev["id"], "resposta": "talvez"})
    assert r.status_code == 400
    assert _ocupa_no_banco(cliente.pool, ev["id"]) is None


def test_a_rota_nao_atravessa_conta(cliente):
    ev = _duvidoso(cliente.pool, conta_id=OUTRA)
    r = cliente.post("/painel/agenda/ocupa",
                     data={"evento_id": ev["id"], "resposta": "ocupa"})
    assert r.json()["ok"] is False
    assert _ocupa_no_banco(cliente.pool, ev["id"]) is None


# ------------------------------------------------------- a caixa do dia

def test_a_caixa_do_dia_recebe_o_veredito_e_a_resposta(cliente):
    """É por estas duas chaves que a caixa decide entre PERGUNTAR e mostrar o
    desfazer. Sem elas no JSON, o JS não teria como saber."""
    ev = _duvidoso(cliente.pool)
    iso = ev["inicio"].astimezone(ag.BRT).date().isoformat()
    d = cliente.get("/painel/agenda/mes?m=" +
                    ev["inicio"].astimezone(ag.BRT).strftime("%Y-%m")).json()
    linha = next(e for e in d["eventos_dia"][iso]["eventos"] if e["id"] == ev["id"])
    assert linha["estado"] == "a_conferir" and linha["ocupa_espaco"] is None
    ag.responder_ocupa(cliente.pool, CONTA, ev["id"], True)
    d2 = cliente.get("/painel/agenda/mes?m=" +
                     ev["inicio"].astimezone(ag.BRT).strftime("%Y-%m")).json()
    linha2 = next(e for e in d2["eventos_dia"][iso]["eventos"] if e["id"] == ev["id"])
    assert linha2["estado"] == "ocupa" and linha2["ocupa_espaco"] is True


def test_os_dois_botoes_e_o_desfazer_existem_no_js():
    js = pa._JS_CRU
    assert "Ocupa — não pode vender" in js
    assert "Não ocupa — o dia segue à venda" in js
    assert "Desfazer" in js and "'limpar'" in js.replace("\\'", "'")
    assert "/painel/agenda/ocupa" in js
