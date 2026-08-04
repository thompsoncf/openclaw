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


def test_responder_mudou_evita_aviso_repetido(pool, conta_id):
    ev = _evento(pool, conta_id)
    conv = cv.criar_convidado(pool, conta_id, ev["id"], "Paulo", "86 98888-7777")
    r1 = cv.responder(pool, conv["token"], "confirmado")
    assert r1["mudou"] is True                    # 1ª resposta: avisa
    r2 = cv.responder(pool, conv["token"], "confirmado")
    assert r2["mudou"] is False                   # re-tocou o MESMO -> não avisa de novo
    r3 = cv.responder(pool, conv["token"], "recusado")
    assert r3["mudou"] is True                    # mudou de verdade -> avisa


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
    ev = _evento(pool, conta_id, titulo="Café")   # _evento() marca local="Online"
    conv = cv.criar_convidado(pool, conta_id, ev["id"], "Carla Silva", "86988887777")
    c = cv.responder(pool, conv["token"], "confirmado")
    txt = cv.confirmacao_texto(c)
    assert "Carla" in txt and "Café" in txt and "confirmada" in txt.lower()
    assert "calend" in txt.lower()                       # traz o link do calendário
    assert "maps" in txt.lower() and "mapa" in txt.lower()   # e o link do local
    c2 = cv.responder(pool, conv["token"], "recusado")
    assert "não vai poder" in cv.confirmacao_texto(c2).lower()


def test_confirmacao_texto_sem_local_nao_traz_mapa(pool, conta_id):
    ev = ag.criar_evento(pool, conta_id, "Ligação", ag.agora_brt() + timedelta(days=1))
    conv = cv.criar_convidado(pool, conta_id, ev["id"], "Léo", "")
    c = cv.responder(pool, conv["token"], "confirmado")
    assert "mapa" not in cv.confirmacao_texto(c).lower()


def test_link_mapa(pool, conta_id):
    com_local = _evento(pool, conta_id)                  # local="Online"
    assert cv.link_mapa(com_local) is not None
    sem_local = ag.criar_evento(pool, conta_id, "Sem local", ag.agora_brt() + timedelta(days=1))
    assert cv.link_mapa(sem_local) is None


def test_enviar_convite_monta_variaveis(pool, conta_id, monkeypatch):
    ev = _evento(pool, conta_id, titulo="Reunião X")
    conv = cv.criar_convidado(pool, conta_id, ev["id"], "Rui", "86 98888-7777")
    capt = {}
    from finance import whatsapp_out as wout

    def fake(c, cid, numero, content_sid, variaveis):
        capt.update(conta_id=cid, numero=numero, sid=content_sid, vars=variaveis)
        return {"ok": True, "sid": "SM1"}

    monkeypatch.setattr(wout, "enviar_template", fake)
    monkeypatch.setenv("TWILIO_TMPL_CONVITE_SID", "HXtest")
    r = cv.enviar_convite_whatsapp(pool, conv["token"])
    assert r["ok"] and capt["sid"] == "HXtest"
    assert capt["conta_id"] == conta_id                # roteia pela conta (nº da empresa)
    assert capt["numero"] == "86 98888-7777"           # nº do convidado
    assert capt["vars"]["1"] == "Reunião X"            # {{1}} assunto/título
    assert capt["vars"]["2"]                            # {{2}} data e horário
    assert "3" not in capt["vars"]                      # só 2 variáveis (bate com o template)


def test_enviar_convite_sem_numero_ou_sem_template(pool, conta_id, monkeypatch):
    ev = _evento(pool, conta_id)
    sem_num = cv.criar_convidado(pool, conta_id, ev["id"], "SemZap", "")
    assert cv.enviar_convite_whatsapp(pool, sem_num["token"])["erro"] == "sem_numero"
    com_num = cv.criar_convidado(pool, conta_id, ev["id"], "ComZap", "86 98888-7777")
    monkeypatch.delenv("TWILIO_TMPL_CONVITE_SID", raising=False)
    assert cv.enviar_convite_whatsapp(pool, com_num["token"])["erro"] == "sem_template"


def test_marcar_evento_com_convidados_dispara(pool, conta_id, monkeypatch):
    """Pulo do gato: marcar reunião + convidar + disparar, tudo pelo chat."""
    from finance.agenda_tools import construir_ferramentas_agenda
    enviados = []
    monkeypatch.setattr(cv, "enviar_convite_whatsapp",
                        lambda p, token: enviados.append(token) or {"ok": True, "sid": "SM1"})
    ferrs = {f.nome: f for f in construir_ferramentas_agenda(pool, conta_id)}
    r = ferrs["marcar_evento"].executar({
        "titulo": "Reunião clínica", "inicio": "28/07/2099 09:00",
        "convidados": [{"nome": "Paulo", "contato": "86 98888-7777"}]})
    assert "Marquei" in r and "Paulo" in r and "enviado" in r.lower()
    assert len(enviados) == 1
    assert cv.pendentes_por_numero(pool, "+5586988887777")     # convite pendente criado


def test_marcar_evento_com_varios_convidados(pool, conta_id, monkeypatch):
    """Grupo pelo chat: um comando convida e dispara pra vários de uma vez."""
    from finance.agenda_tools import construir_ferramentas_agenda
    enviados = []
    monkeypatch.setattr(cv, "enviar_convite_whatsapp",
                        lambda p, token: enviados.append(token) or {"ok": True})
    ferrs = {f.nome: f for f in construir_ferramentas_agenda(pool, conta_id)}
    r = ferrs["marcar_evento"].executar({
        "titulo": "Alinhamento", "inicio": "28/07/2099 14:00",
        "convidados": [{"nome": "Paulo", "contato": "86 98888-7777"},
                       {"nome": "Ana", "contato": "86 97777-6666"}]})
    assert "Paulo" in r and "Ana" in r
    assert len(enviados) == 2                                # disparou pros dois
    assert "👥 Com: Paulo e Ana" in r                          # resumo dos envolvidos pro dono


def test_marcar_evento_com_um_convidado_nao_mostra_resumo(pool, conta_id, monkeypatch):
    """Com só 1 convidado, a linha "Com: ..." não faz sentido (já tem no "Convites")."""
    from finance.agenda_tools import construir_ferramentas_agenda
    monkeypatch.setattr(cv, "enviar_convite_whatsapp", lambda p, t: {"ok": True})
    ferrs = {f.nome: f for f in construir_ferramentas_agenda(pool, conta_id)}
    r = ferrs["marcar_evento"].executar({
        "titulo": "Café", "inicio": "28/07/2099 09:00",
        "convidados": [{"nome": "Paulo", "contato": "86 98888-7777"}]})
    assert "👥 Com:" not in r


def test_marcar_evento_com_local_traz_link_do_mapa(pool, conta_id):
    from finance.agenda_tools import construir_ferramentas_agenda
    ferrs = {f.nome: f for f in construir_ferramentas_agenda(pool, conta_id)}
    r = ferrs["marcar_evento"].executar(
        {"titulo": "Reunião", "inicio": "28/07/2099 10:00", "local": "Escritório"})
    assert "📍 Ver o local no mapa:" in r and "maps" in r.lower()


def test_marcar_evento_sem_local_nao_traz_mapa(pool, conta_id):
    from finance.agenda_tools import construir_ferramentas_agenda
    ferrs = {f.nome: f for f in construir_ferramentas_agenda(pool, conta_id)}
    r = ferrs["marcar_evento"].executar({"titulo": "Ligação", "inicio": "28/07/2099 10:00"})
    assert "mapa" not in r.lower()


def test_remarcar_evento_com_local_traz_link_do_mapa(pool, conta_id):
    from finance.agenda_tools import construir_ferramentas_agenda
    ferrs = {f.nome: f for f in construir_ferramentas_agenda(pool, conta_id)}
    ferrs["marcar_evento"].executar(
        {"titulo": "Consulta", "inicio": "28/07/2099 10:00", "local": "Clínica Central"})
    r = ferrs["remarcar_evento"].executar({"titulo": "Consulta", "novo_inicio": "29/07/2099 11:00"})
    assert "📍 Ver o local no mapa:" in r


def test_convidar_reuniao_em_evento_existente(pool, conta_id, monkeypatch):
    from finance.agenda_tools import construir_ferramentas_agenda
    monkeypatch.setattr(cv, "enviar_convite_whatsapp", lambda p, t: {"ok": True})
    ferrs = {f.nome: f for f in construir_ferramentas_agenda(pool, conta_id)}
    ferrs["marcar_evento"].executar({"titulo": "Alinhamento semanal", "inicio": "29/07/2099 10:00"})
    r = ferrs["convidar_reuniao"].executar(
        {"titulo": "Alinhamento semanal", "convidados": [{"nome": "Bia", "contato": "86 97777-6666"}]})
    assert "Bia" in r and "enviado" in r.lower()


def test_marcar_com_convidado_sem_numero_da_link(pool, conta_id):
    from finance.agenda_tools import construir_ferramentas_agenda
    ferrs = {f.nome: f for f in construir_ferramentas_agenda(pool, conta_id)}
    r = ferrs["marcar_evento"].executar(
        {"titulo": "Café", "inicio": "30/07/2099 08:00", "convidados": [{"nome": "SemZap"}]})
    assert "SemZap" in r and "/convite/" in r        # sem número -> devolve o link pra mandar


class _FakeCur:
    """Cursor fake pra testar a resolução do número da empresa sem banco."""
    def __init__(self, row):
        self._row = row

    def execute(self, *a, **k):
        return self

    def fetchone(self):
        return self._row


def test_whatsapp_out_template_usa_numero_da_empresa(monkeypatch):
    from finance import whatsapp_out as wout, whatsapp_twilio as wa
    capt = {}
    monkeypatch.setattr(wa, "enviar_template",
                        lambda rem, num, sid, v: capt.update(rem=rem, num=num) or {"ok": True})
    # empresa com número Twilio próprio -> usa ELE (final 7678)
    r = wout.enviar_template(_FakeCur(("twilio", "whatsapp:+5586990007678", None, None)),
                             1, "86 98888-7777", "HXx", {"1": "a", "2": "b"})
    assert r["ok"] and capt["rem"] == "whatsapp:+5586990007678"
    # sem canal configurado -> cai pro número global do Zaq
    monkeypatch.setenv("TWILIO_WHATSAPP_FROM", "whatsapp:+5586990000000")
    capt.clear()
    wout.enviar_template(_FakeCur(None), 1, "86999", "HXx", {"1": "a", "2": "b"})
    assert capt["rem"] == "whatsapp:+5586990000000"
    # provedor cloud -> roteia pro Cloud API (o campo do template vira o NOME do template)
    from finance import whatsapp_cloud as wcloud
    captc = {}
    monkeypatch.setattr(wcloud, "enviar_template",
                        lambda pid, tok, num, nome, v, mmlite=False:
                        captc.update(pid=pid, nome=nome, mm=mmlite) or {"ok": True})
    r2 = wout.enviar_template(_FakeCur(("cloud", None, "PID", "tok")), 1, "86999", "meu_template", {})
    assert r2["ok"] and captc["pid"] == "PID" and captc["nome"] == "meu_template"
    assert captc["mm"] is False   # sem flag = Cloud API comum
    # com mmlite=True o flag chega no adaptador cloud (roteia pra MM Lite)
    wout.enviar_template(_FakeCur(("cloud", None, "PID", "tok")), 1, "86999", "meu_template", {},
                         mmlite=True)
    assert captc["mm"] is True


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


def test_vocabulario_stt_traz_titulos_e_convidados(pool, conta_id):
    from finance import agenda as ag
    from datetime import timedelta
    ev = ag.criar_evento(pool, conta_id, "Reunião com Mailson",
                         ag.agora_brt() + timedelta(days=1), tipo="empresa")
    cv.criar_convidado(pool, conta_id, ev["id"], "Mailson Souza", "86 98888-7777")
    voc = ag.vocabulario_stt(pool, conta_id)
    assert "Reunião com Mailson" in voc and "Mailson Souza" in voc
