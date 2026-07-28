"""Convidados de reunião + confirmação por link público (finance/convites.py).

Roda com banco de TESTE separado (ver tests/conftest.py).
"""
import os
from datetime import timedelta
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from finance import agenda as ag
from finance import convites as cv


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
        cid = c.execute("insert into contas (tipo, nome) values ('pj','Padaria Central') returning id").fetchone()[0]
        c.commit()
    return cid


def _evento(pool, conta_id, titulo="Reunião de fechamento"):
    return ag.criar_evento(pool, conta_id, titulo,
                           ag.agora_brt() + timedelta(days=1), tipo="empresa",
                           local="Online")


def test_criar_e_resolver_por_token(pool, conta_id):
    ev = _evento(pool, conta_id)
    conv = cv.criar_convidado(pool, conta_id, ev["id"], "João da Padaria", "(86) 99999-0000")
    assert conv["token"] and conv["status"] == "pendente"
    c = cv.por_token(pool, conv["token"])
    assert c is not None
    assert c["nome"] == "João da Padaria" and c["conta_id"] == conta_id
    assert c["evento"]["titulo"] == "Reunião de fechamento"
    assert c["empresa"] == "Padaria Central"          # nome da empresa vem junto


def test_por_token_desconhecido_volta_none(pool, conta_id):
    assert cv.por_token(pool, "nao-existe") is None
    assert cv.por_token(pool, "") is None


def test_responder_confirma_e_reflete(pool, conta_id):
    ev = _evento(pool, conta_id)
    conv = cv.criar_convidado(pool, conta_id, ev["id"], "Maria", "")
    r = cv.responder(pool, conv["token"], "confirmado")
    assert r is not None and r["status"] == "confirmado"
    # persistiu
    assert cv.por_token(pool, conv["token"])["status"] == "confirmado"


def test_responder_remarcar_guarda_resposta(pool, conta_id):
    ev = _evento(pool, conta_id)
    conv = cv.criar_convidado(pool, conta_id, ev["id"], "Ana", "")
    r = cv.responder(pool, conv["token"], "remarcar", "Podia ser 16h?")
    assert r["status"] == "remarcar" and r["resposta"] == "Podia ser 16h?"


def test_responder_status_invalido_ou_token_ruim(pool, conta_id):
    ev = _evento(pool, conta_id)
    conv = cv.criar_convidado(pool, conta_id, ev["id"], "Zé", "")
    assert cv.responder(pool, conv["token"], "pendente") is None   # não pode "des-responder"
    assert cv.responder(pool, conv["token"], "qualquer") is None   # status inválido
    assert cv.responder(pool, "token-ruim", "confirmado") is None  # token inexistente


def test_por_evento_agrupa_e_isola_por_conta(pool, conta_id):
    ev = _evento(pool, conta_id)
    cv.criar_convidado(pool, conta_id, ev["id"], "A", "")
    cv.criar_convidado(pool, conta_id, ev["id"], "B", "")
    mapa = cv.por_evento(pool, conta_id, [ev["id"]])
    assert len(mapa[ev["id"]]) == 2
    # outra conta não enxerga
    with pool.connection() as c:
        outra = c.execute("insert into contas (tipo,nome) values ('pf','Outra') returning id").fetchone()[0]
        c.commit()
    assert cv.por_evento(pool, outra, [ev["id"]]) == {}


def test_evento_por_id(pool, conta_id):
    ev = _evento(pool, conta_id, titulo="Alinhamento")
    got = ag.evento_por_id(pool, conta_id, ev["id"])
    assert got and got["titulo"] == "Alinhamento" and got["tipo"] == "empresa"
    assert ag.evento_por_id(pool, conta_id, 999999) is None
    # outra conta não pega o evento
    with pool.connection() as c:
        outra = c.execute("insert into contas (tipo,nome) values ('pf','Outra') returning id").fetchone()[0]
        c.commit()
    assert ag.evento_por_id(pool, outra, ev["id"]) is None


def test_rsvp_por_texto_mapeia_botoes():
    assert cv.rsvp_por_texto("✅ Confirmar") == "confirmado"
    assert cv.rsvp_por_texto("CONFIRMAR") == "confirmado"
    assert cv.rsvp_por_texto("🔁 Remarcar") == "remarcar"
    assert cv.rsvp_por_texto("❌ Não vou poder") == "recusado"
    assert cv.rsvp_por_texto("nao vou poder") == "recusado"
    # não-RSVP não dispara (protege o fluxo normal do agente)
    assert cv.rsvp_por_texto("uber 22") is None
    assert cv.rsvp_por_texto("oi, tudo bem?") is None
    assert cv.rsvp_por_texto("") is None


def test_pendentes_por_numero_casa_por_sufixo(pool, conta_id):
    ev = _evento(pool, conta_id, titulo="Reunião com o número")
    conv = cv.criar_convidado(pool, conta_id, ev["id"], "Bia", "(86) 98111-1111")
    # quem responde chega como +55 86 98111-1111 (DDI + 9º dígito) e ainda casa
    achados = cv.pendentes_por_numero(pool, "+5586981111111")
    assert conv["token"] in {a["token"] for a in achados}
    # número diferente não casa com este convite
    assert conv["token"] not in {a["token"] for a in cv.pendentes_por_numero(pool, "+5511970000000")}
    # depois de responder, deixa de ser pendente
    cv.responder(pool, conv["token"], "confirmado")
    assert conv["token"] not in {a["token"] for a in cv.pendentes_por_numero(pool, "+5586981111111")}


def test_pendentes_ignora_evento_passado(pool, conta_id):
    with pool.connection() as c:
        # evento no passado -> não deve casar (janela de -2h)
        eid = c.execute(
            "insert into eventos_agenda (conta_id, titulo, inicio, tipo) "
            "values (%s,%s, now() - interval '1 day', 'empresa') returning id",
            (conta_id, "Já passou")).fetchone()[0]
        c.commit()
    conv = cv.criar_convidado(pool, conta_id, eid, "Léo", "86 98222-2222")
    assert conv["token"] not in {a["token"] for a in cv.pendentes_por_numero(pool, "+5586982222222")}


def test_confirmacao_texto_por_status(pool, conta_id):
    ev = _evento(pool, conta_id, titulo="Café")
    conv = cv.criar_convidado(pool, conta_id, ev["id"], "Carla Silva", "86988887777")
    c = cv.responder(pool, conv["token"], "confirmado")
    txt = cv.confirmacao_texto(c)
    assert "Carla" in txt and "Café" in txt and "confirmada" in txt.lower()
    assert "calend" in txt.lower()                       # traz o link do calendário
    c2 = cv.responder(pool, conv["token"], "recusado")
    assert "não vai poder" in cv.confirmacao_texto(c2).lower()


def test_enviar_convite_monta_variaveis(pool, conta_id, monkeypatch):
    ev = _evento(pool, conta_id, titulo="Reunião X")
    conv = cv.criar_convidado(pool, conta_id, ev["id"], "Rui", "86 98888-7777")
    capt = {}
    from finance import whatsapp_twilio as wa

    def fake(remetente, numero, content_sid, variaveis):
        capt.update(remetente=remetente, numero=numero, sid=content_sid, vars=variaveis)
        return {"ok": True, "sid": "SM1"}

    monkeypatch.setattr(wa, "enviar_template", fake)
    monkeypatch.setenv("TWILIO_TMPL_CONVITE_SID", "HXtest")
    monkeypatch.setenv("TWILIO_WHATSAPP_FROM", "whatsapp:+5586990000000")
    r = cv.enviar_convite_whatsapp(pool, conv["token"])
    assert r["ok"] and capt["sid"] == "HXtest"
    assert capt["vars"]["1"] == "Padaria Central"     # quem convida (empresa)
    assert capt["vars"]["2"] == "Reunião X"           # título
    assert capt["vars"]["3"]                           # quando preenchido
    assert capt["numero"] == "86 98888-7777"           # o adaptador normaliza depois


def test_enviar_convite_sem_numero_ou_sem_template(pool, conta_id, monkeypatch):
    ev = _evento(pool, conta_id)
    sem_num = cv.criar_convidado(pool, conta_id, ev["id"], "SemZap", "")
    assert cv.enviar_convite_whatsapp(pool, sem_num["token"])["erro"] == "sem_numero"
    com_num = cv.criar_convidado(pool, conta_id, ev["id"], "ComZap", "86 98888-7777")
    monkeypatch.delenv("TWILIO_TMPL_CONVITE_SID", raising=False)
    assert cv.enviar_convite_whatsapp(pool, com_num["token"])["erro"] == "sem_template"


def test_grupo_resumo_e_fechamento(pool, conta_id):
    ev = _evento(pool, conta_id)
    ca = cv.criar_convidado(pool, conta_id, ev["id"], "A", "")
    cb = cv.criar_convidado(pool, conta_id, ev["id"], "B", "")
    cc = cv.criar_convidado(pool, conta_id, ev["id"], "C", "")
    gs = cv.por_evento(pool, conta_id, [ev["id"]])[ev["id"]]
    r = cv.resumo(gs)
    assert r["total"] == 3 and r["confirmados"] == 0 and r["fechado"] is False
    cv.responder(pool, ca["token"], "confirmado")
    cv.responder(pool, cb["token"], "confirmado")
    gs = cv.por_evento(pool, conta_id, [ev["id"]])[ev["id"]]
    r = cv.resumo(gs)
    assert r["confirmados"] == 2 and r["respondidos"] == 2 and r["fechado"] is False
    cv.responder(pool, cc["token"], "recusado")          # último responde -> fecha
    r = cv.resumo(cv.por_evento(pool, conta_id, [ev["id"]])[ev["id"]])
    assert r["fechado"] is True and r["confirmados"] == 2 and r["recusados"] == 1
