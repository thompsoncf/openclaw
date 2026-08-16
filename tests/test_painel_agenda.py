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
        for nome in ("098_agenda.sql", "099_agenda_tipo.sql", "100_evento_convidados.sql",
                    "130_evento_desfecho.sql", "131_evento_link_online.sql", "132_convidado_canal_resposta.sql",
                    "139_agenda_mensagens_log.sql", "146_agenda_enviar_confirmacao.sql",
                    "160_agenda_pre_reserva.sql", "163_evento_sinal_esperado.sql"):
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


class _FakeSessionRequest:
    def __init__(self, conta_id):
        self.session = {"conta_id": conta_id}


def test_rota_desfecho_marca_e_recusa_evento_futuro(pool, conta_id, monkeypatch):
    from web import painel_agenda as pa
    monkeypatch.setattr(pa, "get_pool", lambda: pool)
    monkeypatch.setattr(pa, "_acesso", lambda req: (
        {"conta_id": conta_id, "gerencia": True, "membro_id": None}, None))
    passado = ag.criar_evento(pool, conta_id, "Reunião de ontem", ag.agora_brt() - timedelta(days=1))
    futuro = ag.criar_evento(pool, conta_id, "Reunião de amanhã", ag.agora_brt() + timedelta(days=1))
    r1 = pa.agenda_desfecho(_FakeSessionRequest(conta_id), evento_id=passado["id"], desfecho="realizado")
    assert r1.status_code == 200
    assert ag.evento_por_id(pool, conta_id, passado["id"])["desfecho"] == "realizado"
    r2 = pa.agenda_desfecho(_FakeSessionRequest(conta_id), evento_id=futuro["id"], desfecho="realizado")
    assert ag.evento_por_id(pool, conta_id, futuro["id"])["desfecho"] is None   # não marca o futuro


def test_rota_remarcar_reaproveita_cancelado(pool, conta_id, monkeypatch):
    from web import painel_agenda as pa
    monkeypatch.setattr(pa, "get_pool", lambda: pool)
    monkeypatch.setattr(pa, "_acesso", lambda req: (
        {"conta_id": conta_id, "gerencia": True, "membro_id": None}, None))
    ev = ag.criar_evento(pool, conta_id, "Cancelada pra reaproveitar", ag.agora_brt() - timedelta(days=5))
    ag.cancelar_evento(pool, conta_id, ev["id"])
    novo = (ag.agora_brt() + timedelta(days=2)).replace(hour=15, minute=0, second=0, microsecond=0)
    resp = pa.agenda_remarcar(_FakeSessionRequest(conta_id), evento_id=ev["id"],
                              data=novo.strftime("%Y-%m-%d"), hora="15:00", avisar="1", m="")
    assert resp.status_code == 303
    ev2 = ag.evento_por_id(pool, conta_id, ev["id"])
    assert ev2 is not None and ev2["inicio"] == novo


def test_rota_novo_salva_link_online_quando_marcado_online(pool, conta_id, monkeypatch):
    from web import painel_agenda as pa
    monkeypatch.setattr(pa, "get_pool", lambda: pool)
    monkeypatch.setattr(pa, "_acesso", lambda req: (
        {"conta_id": conta_id, "gerencia": True, "membro_id": None}, None))
    amanha = ag.agora_brt() + timedelta(days=1)
    resp = pa.agenda_novo(_FakeSessionRequest(conta_id), titulo="Daily",
                          data=amanha.strftime("%Y-%m-%d"), hora="09:00", hora_fim="",
                          descricao="",
                          tipo="pessoal", local="Online",
                          link_online="https://meet.google.com/abc-defg-hij",
                          convidado_nome=[], convidado_contato=[], m="")
    assert resp.status_code == 303
    evs = [e for e in ag.proximos(pool, conta_id) if e["titulo"] == "Daily"]
    assert evs and evs[0]["link_online"] == "https://meet.google.com/abc-defg-hij"


def test_rota_convidado_adicionar_inclui_em_evento_ja_marcado(pool, conta_id, monkeypatch):
    """Antes só dava pra adicionar convidado na hora de criar o compromisso — essa
    rota permite incluir mais gente depois. Redireciona com convite_ev= pra já
    abrir o card de compartilhar (mesmo comportamento de quando cria com convidados)."""
    from web import painel_agenda as pa
    monkeypatch.setattr(pa, "get_pool", lambda: pool)
    monkeypatch.setattr(pa, "_acesso", lambda req: (
        {"conta_id": conta_id, "gerencia": True, "membro_id": None}, None))
    ev = ag.criar_evento(pool, conta_id, "Reunião de fechamento", ag.agora_brt() + timedelta(days=1))
    resp = pa.agenda_convidado_adicionar(_FakeSessionRequest(conta_id), evento_id=ev["id"],
                                         nome="Carlos", contato="86988887777", m="2026-08")
    assert resp.status_code == 303
    assert f"convite_ev={ev['id']}" in resp.headers["location"]
    convidados = cv.por_evento(pool, conta_id, [ev["id"]]).get(ev["id"], [])
    assert len(convidados) == 1
    assert convidados[0]["nome"] == "Carlos" and convidados[0]["contato"] == "86988887777"
    assert convidados[0]["status"] == "pendente"


def test_rota_convidado_adicionar_exige_nome_ou_contato(pool, conta_id, monkeypatch):
    from web import painel_agenda as pa
    monkeypatch.setattr(pa, "get_pool", lambda: pool)
    monkeypatch.setattr(pa, "_acesso", lambda req: (
        {"conta_id": conta_id, "gerencia": True, "membro_id": None}, None))
    ev = ag.criar_evento(pool, conta_id, "Sem convidado nenhum", ag.agora_brt() + timedelta(days=1))
    resp = pa.agenda_convidado_adicionar(_FakeSessionRequest(conta_id), evento_id=ev["id"],
                                         nome="", contato="", m="")
    assert resp.status_code == 303
    assert "convite_ev" not in resp.headers["location"]
    assert cv.por_evento(pool, conta_id, [ev["id"]]).get(ev["id"], []) == []


def test_rota_convidado_adicionar_recusa_evento_cancelado(pool, conta_id, monkeypatch):
    from web import painel_agenda as pa
    monkeypatch.setattr(pa, "get_pool", lambda: pool)
    monkeypatch.setattr(pa, "_acesso", lambda req: (
        {"conta_id": conta_id, "gerencia": True, "membro_id": None}, None))
    ev = ag.criar_evento(pool, conta_id, "Vou cancelar", ag.agora_brt() + timedelta(days=1))
    ag.cancelar_evento(pool, conta_id, ev["id"])
    resp = pa.agenda_convidado_adicionar(_FakeSessionRequest(conta_id), evento_id=ev["id"],
                                         nome="Carlos", contato="", m="")
    assert resp.status_code == 303
    assert cv.por_evento(pool, conta_id, [ev["id"]]).get(ev["id"], []) == []


def test_rota_historico_lista_e_filtra(pool, conta_id, monkeypatch):
    from web import painel_agenda as pa
    monkeypatch.setattr(pa, "get_pool", lambda: pool)
    monkeypatch.setattr(pa, "_acesso", lambda req: (
        {"conta_id": conta_id, "gerencia": True, "membro_id": None}, None))
    ev = ag.criar_evento(pool, conta_id, "Reunião com Paulo", ag.agora_brt() + timedelta(days=1))
    conv = cv.criar_convidado(pool, conta_id, ev["id"], "Paulo", "86999382687")
    cv.registrar_mensagem(pool, conta_id, ev["id"], conv["id"], "lembrete", "whatsapp_template",
                          False, "sem_template")
    resp = pa.agenda_historico(_FakeSessionRequest(conta_id))
    import json
    body = json.loads(resp.body)
    assert body["ok"] is True and body["total"] == 1
    item = body["itens"][0]
    assert item["convidado_rot"] == "Paulo" and item["motivo_rot"] == "sem template configurado"
    assert item["pode_reenviar"] is True

    resp2 = pa.agenda_historico(_FakeSessionRequest(conta_id), falhas="0", q="não existe")
    assert json.loads(resp2.body)["total"] == 0


def test_rota_historico_reenviar_dispara_de_novo(pool, conta_id, monkeypatch):
    from web import painel_agenda as pa
    monkeypatch.setattr(pa, "get_pool", lambda: pool)
    monkeypatch.setattr(pa, "_acesso", lambda req: (
        {"conta_id": conta_id, "gerencia": True, "membro_id": None}, None))
    ev = ag.criar_evento(pool, conta_id, "Reunião Z", ag.agora_brt() + timedelta(days=1))
    conv = cv.criar_convidado(pool, conta_id, ev["id"], "Léo", "86988883333")
    monkeypatch.delenv("TWILIO_TMPL_CONVITE_SID", raising=False)
    cv.enviar_convite_whatsapp(pool, conv["token"])
    log_id = cv.listar_historico(pool, conta_id)["itens"][0]["id"]

    from finance import whatsapp_out as wout
    monkeypatch.setattr(wout, "enviar_template", lambda c, cid, n, sid, v: {"ok": True, "sid": "SM3"})
    monkeypatch.setenv("TWILIO_TMPL_CONVITE_SID", "HXtest")
    import json
    resp = pa.agenda_historico_reenviar(_FakeSessionRequest(conta_id), log_id=log_id)
    assert json.loads(resp.body)["ok"] is True


def test_rota_novo_ignora_link_online_se_nao_marcou_online(pool, conta_id, monkeypatch):
    """Defesa no servidor: mesmo que o cliente mande link_online preenchido (o JS
    já limpa ao trocar de modo, mas não confia cegamente no que vem do form),
    só grava se local for realmente 'Online'."""
    from web import painel_agenda as pa
    monkeypatch.setattr(pa, "get_pool", lambda: pool)
    monkeypatch.setattr(pa, "_acesso", lambda req: (
        {"conta_id": conta_id, "gerencia": True, "membro_id": None}, None))
    amanha = ag.agora_brt() + timedelta(days=1)
    resp = pa.agenda_novo(_FakeSessionRequest(conta_id), titulo="Presencial",
                          data=amanha.strftime("%Y-%m-%d"), hora="09:00", hora_fim="",
                          descricao="",
                          tipo="pessoal", local="Escritório",
                          link_online="https://meet.google.com/deveria-sumir",
                          convidado_nome=[], convidado_contato=[], m="")
    assert resp.status_code == 303
    evs = [e for e in ag.proximos(pool, conta_id) if e["titulo"] == "Presencial"]
    assert evs and evs[0]["link_online"] is None
