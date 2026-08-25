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
        for nome in ("058_dados_empresa.sql", "081_canais_config.sql", "084_canal_token.sql", "096_whatsapp_cloud.sql", "145_canal_templates_agenda.sql",
                    "098_agenda.sql", "099_agenda_tipo.sql", "100_evento_convidados.sql",
                    "130_evento_desfecho.sql", "131_evento_link_online.sql", "132_convidado_canal_resposta.sql",
                    "139_agenda_mensagens_log.sql", "146_agenda_enviar_confirmacao.sql",
                    "160_agenda_pre_reserva.sql", "163_evento_sinal_esperado.sql",
                    "179_agenda_tipo_e_hora_sugerida.sql"):
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
                           local="Escritório Central")


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
    ev = _evento(pool, conta_id, titulo="Café")   # _evento() marca um local físico
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
    com_local = _evento(pool, conta_id)                  # local físico de verdade
    assert cv.link_mapa(com_local) is not None
    sem_local = ag.criar_evento(pool, conta_id, "Sem local", ag.agora_brt() + timedelta(days=1))
    assert cv.link_mapa(sem_local) is None


def test_link_mapa_reuniao_online_nao_traz_mapa():
    """Botão "reunião online" do form marca local="Online" — não é endereço
    nenhum, então nunca deve virar link de mapa (nem no convite, nem quando o
    convidado confirma presença)."""
    ev = {"local": "Online"}
    assert cv.link_mapa(ev) is None


def test_confirmacao_texto_reuniao_online_nao_traz_mapa(pool, conta_id):
    ev = ag.criar_evento(pool, conta_id, "Daily", ag.agora_brt() + timedelta(days=1), local="Online")
    conv = cv.criar_convidado(pool, conta_id, ev["id"], "Léo", "86988887777")
    c = cv.responder(pool, conv["token"], "confirmado")
    txt = cv.confirmacao_texto(c)
    assert "confirmada" in txt.lower()
    assert "mapa" not in txt.lower() and "maps" not in txt.lower()


def test_confirmacao_texto_traz_link_da_chamada(pool, conta_id):
    ev = ag.criar_evento(pool, conta_id, "Daily", ag.agora_brt() + timedelta(days=1),
                         local="Online", link_online="https://meet.google.com/abc-defg-hij")
    conv = cv.criar_convidado(pool, conta_id, ev["id"], "Léo", "86988887777")
    c = cv.responder(pool, conv["token"], "confirmado")
    txt = cv.confirmacao_texto(c)
    assert "meet.google.com/abc-defg-hij" in txt
    assert "entrar na chamada" in txt.lower()


def test_confirmacao_texto_sem_link_online_nao_traz_linha(pool, conta_id):
    ev = _evento(pool, conta_id, titulo="Reunião presencial")
    conv = cv.criar_convidado(pool, conta_id, ev["id"], "Léo", "86988887777")
    c = cv.responder(pool, conv["token"], "confirmado")
    txt = cv.confirmacao_texto(c)
    assert "chamada" not in txt.lower()


def test_por_token_traz_link_online(pool, conta_id):
    ev = ag.criar_evento(pool, conta_id, "Daily", ag.agora_brt() + timedelta(days=1),
                         local="Online", link_online="https://meet.google.com/abc-defg-hij")
    conv = cv.criar_convidado(pool, conta_id, ev["id"], "Léo", "")
    c = cv.por_token(pool, conv["token"])
    assert c["evento"]["link_online"] == "https://meet.google.com/abc-defg-hij"


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
    assert capt["vars"]["1"] == "Reunião X — Padaria Central"  # {{1}}: título + empresa
    assert capt["vars"]["2"]                            # {{2}} data e horário
    assert "3" not in capt["vars"]                      # só 2 variáveis (bate com o template)


def test_enviar_convite_cita_outros_convidados_no_titulo(pool, conta_id, monkeypatch):
    """Com 2+ convidados no mesmo evento, {{1}} também cita quem mais vem —
    mesmo texto pra todo mundo, incluindo o próprio destinatário (é assim que
    já funciona na mensagem manual de compartilhar)."""
    ev = _evento(pool, conta_id, titulo="Reunião X")
    cv.criar_convidado(pool, conta_id, ev["id"], "Ana", "86 99111-2233")
    conv = cv.criar_convidado(pool, conta_id, ev["id"], "Carlos", "86 99222-3344")
    capt = {}
    from finance import whatsapp_out as wout

    def fake(c, cid, numero, content_sid, variaveis):
        capt.update(vars=variaveis)
        return {"ok": True, "sid": "SM1"}

    monkeypatch.setattr(wout, "enviar_template", fake)
    monkeypatch.setenv("TWILIO_TMPL_CONVITE_SID", "HXtest")
    cv.enviar_convite_whatsapp(pool, conv["token"])
    assert capt["vars"]["1"] == "Reunião X — Padaria Central (com Ana e Carlos)"


def test_enviar_convite_sem_numero_ou_sem_template(pool, conta_id, monkeypatch):
    ev = _evento(pool, conta_id)
    sem_num = cv.criar_convidado(pool, conta_id, ev["id"], "SemZap", "")
    assert cv.enviar_convite_whatsapp(pool, sem_num["token"])["erro"] == "sem_numero"
    com_num = cv.criar_convidado(pool, conta_id, ev["id"], "ComZap", "86 98888-7777")
    monkeypatch.delenv("TWILIO_TMPL_CONVITE_SID", raising=False)
    assert cv.enviar_convite_whatsapp(pool, com_num["token"])["erro"] == "sem_template"


def _salvar_tmpl(pool, conta_id, convite=None, lembrete=None):
    """Mesmo upsert da rota /comunicacao/canal-templates."""
    with pool.connection() as c:
        c.execute("""insert into canais_config (conta_id, canal, identificador, provedor, ativo,
                                                tmpl_convite_sid, tmpl_lembrete_sid)
                     values (%s,'whatsapp',%s,'twilio',true,%s,%s)
                     on conflict (conta_id, canal) do update set
                       tmpl_convite_sid=excluded.tmpl_convite_sid,
                       tmpl_lembrete_sid=excluded.tmpl_lembrete_sid""",
                  (conta_id, f"whatsapp:+5586{conta_id:07d}", convite, lembrete))
        c.commit()


def test_sid_da_empresa_vence_a_env(pool, conta_id, monkeypatch):
    """O template é aprovado dentro da conta do NÚMERO — o SID da empresa tem que
    ganhar da env global, senão uma conta mandaria com o template de outra."""
    monkeypatch.setenv("TWILIO_TMPL_CONVITE_SID", "HXdaEnv")
    monkeypatch.setenv("TWILIO_TMPL_LEMBRETE_SID", "HXlembreteEnv")
    _salvar_tmpl(pool, conta_id, convite="HXdaEmpresa", lembrete="HXlembreteEmpresa")
    assert cv.sid_convite(pool, conta_id) == "HXdaEmpresa"
    assert cv.sid_lembrete(pool, conta_id) == "HXlembreteEmpresa"


def test_sem_sid_da_empresa_cai_na_env(pool, conta_id, monkeypatch):
    """Fallback: quem já rodava só com a env continua funcionando igual."""
    monkeypatch.setenv("TWILIO_TMPL_CONVITE_SID", "HXdaEnv")
    monkeypatch.setenv("TWILIO_TMPL_LEMBRETE_SID", "HXlembreteEnv")
    _salvar_tmpl(pool, conta_id, convite=None, lembrete=None)
    assert cv.sid_convite(pool, conta_id) == "HXdaEnv"
    assert cv.sid_lembrete(pool, conta_id) == "HXlembreteEnv"


def test_sid_nao_vaza_entre_empresas(pool, conta_id, monkeypatch):
    monkeypatch.delenv("TWILIO_TMPL_CONVITE_SID", raising=False)
    _salvar_tmpl(pool, conta_id, convite="HXsoDaPrimeira")
    with pool.connection() as c:
        outra = c.execute("insert into contas (tipo,nome) values ('pj','Outra') returning id").fetchone()[0]
        c.commit()
    assert cv.sid_convite(pool, conta_id) == "HXsoDaPrimeira"
    assert cv.sid_convite(pool, outra) == ""          # sem coluna e sem env


def test_enviar_convite_usa_o_sid_da_empresa(pool, conta_id, monkeypatch):
    monkeypatch.delenv("TWILIO_TMPL_CONVITE_SID", raising=False)
    _salvar_tmpl(pool, conta_id, convite="HXdaEmpresa")
    ev = _evento(pool, conta_id)
    conv = cv.criar_convidado(pool, conta_id, ev["id"], "Rui", "86988887777")
    from finance import whatsapp_out as wout
    capt = {}
    monkeypatch.setattr(wout, "enviar_template",
                        lambda c, i, n, sid, v, mmlite=False: capt.update(sid=sid) or {"ok": True})
    assert cv.enviar_convite_whatsapp(pool, conv["token"])["ok"] is True
    assert capt["sid"] == "HXdaEmpresa"               # não a env (que nem existe aqui)


class _FakeReq:
    def __init__(self):
        self.session = {}


def test_rota_canal_templates_salva_e_limpa(pool, conta_id, monkeypatch):
    """Diferente da rota do número (onde vazio MANTÉM o que estava, pra proteger
    token), aqui vazio LIMPA — o SID aparece no campo, então precisa haver como
    remover um errado."""
    from web import painel_prospeccao as pp
    monkeypatch.setattr(pp, "get_pool", lambda: pool)
    monkeypatch.setattr(pp, "_acesso", lambda req: (
        {"conta_id": conta_id, "gerencia": True, "membro_id": None}, None))
    monkeypatch.delenv("TWILIO_TMPL_CONVITE_SID", raising=False)
    monkeypatch.delenv("TWILIO_TMPL_LEMBRETE_SID", raising=False)
    _salvar_tmpl(pool, conta_id)                          # canal já configurado

    r = pp.comunicacao_canal_templates(_FakeReq(), tmpl_convite_sid="  HXconv  ",
                                       tmpl_lembrete_sid="HXlemb")
    assert r.status_code == 303
    assert cv.sid_convite(pool, conta_id) == "HXconv"     # com strip
    assert cv.sid_lembrete(pool, conta_id) == "HXlemb"

    pp.comunicacao_canal_templates(_FakeReq(), tmpl_convite_sid="", tmpl_lembrete_sid="")
    assert cv.sid_convite(pool, conta_id) == ""           # vazio limpou
    assert cv.sid_lembrete(pool, conta_id) == ""


def test_rota_canal_templates_so_gerencia(pool, conta_id, monkeypatch):
    from web import painel_prospeccao as pp
    monkeypatch.setattr(pp, "get_pool", lambda: pool)
    monkeypatch.setattr(pp, "_acesso", lambda req: (
        {"conta_id": conta_id, "gerencia": False, "membro_id": 9}, None))
    monkeypatch.delenv("TWILIO_TMPL_CONVITE_SID", raising=False)
    _salvar_tmpl(pool, conta_id)
    pp.comunicacao_canal_templates(_FakeReq(), tmpl_convite_sid="HXnaoDeviaSalvar",
                                   tmpl_lembrete_sid="")
    assert cv.sid_convite(pool, conta_id) == ""


def test_rota_canal_templates_sem_canal_configurado_avisa(pool, conta_id, monkeypatch):
    """Sem WhatsApp configurado não dá pra criar a linha aqui (identificador é
    NOT NULL e entra no índice único) — avisa em vez de estourar."""
    from web import painel_prospeccao as pp
    monkeypatch.setattr(pp, "get_pool", lambda: pool)
    monkeypatch.setattr(pp, "_acesso", lambda req: (
        {"conta_id": conta_id, "gerencia": True, "membro_id": None}, None))
    req = _FakeReq()
    r = pp.comunicacao_canal_templates(req, tmpl_convite_sid="HXalgo", tmpl_lembrete_sid="")
    assert r.status_code == 303
    assert "Configure o WhatsApp" in req.session["prosp_aviso"]


def test_enviar_convite_loga_sucesso_e_falha_no_historico(pool, conta_id, monkeypatch):
    """Toda tentativa de envio (sucesso ou falha) fica registrada no Histórico de
    envios — era exatamente essa visibilidade que faltava pra investigar o caso
    do aviso que não chegou pro convidado sem precisar ir direto no banco."""
    ev = _evento(pool, conta_id, titulo="Reunião X")
    conv = cv.criar_convidado(pool, conta_id, ev["id"], "Rui", "86 98888-7777")
    from finance import whatsapp_out as wout
    monkeypatch.setattr(wout, "enviar_template", lambda c, cid, n, sid, v: {"ok": True, "sid": "SM1"})
    monkeypatch.setenv("TWILIO_TMPL_CONVITE_SID", "HXtest")
    cv.enviar_convite_whatsapp(pool, conv["token"])
    hist = cv.listar_historico(pool, conta_id)
    assert hist["total"] == 1
    it = hist["itens"][0]
    assert it["tipo"] == "convite" and it["ok"] is True and it["motivo"] is None
    assert it["convidado_nome"] == "Rui" and it["evento_titulo"] == "Reunião X"

    monkeypatch.delenv("TWILIO_TMPL_CONVITE_SID", raising=False)
    cv.enviar_convite_whatsapp(pool, conv["token"])
    hist2 = cv.listar_historico(pool, conta_id)
    assert hist2["total"] == 2
    falha = hist2["itens"][0]                    # mais recente primeiro
    assert falha["ok"] is False and falha["motivo"] == "sem_template"


def test_listar_historico_filtra_por_falhas_periodo_e_busca(pool, conta_id, monkeypatch):
    ev = _evento(pool, conta_id, titulo="Reunião com Paulo")
    conv = cv.criar_convidado(pool, conta_id, ev["id"], "Paulo", "86988887777")
    monkeypatch.delenv("TWILIO_TMPL_CONVITE_SID", raising=False)
    cv.enviar_convite_whatsapp(pool, conv["token"])       # falha: sem_template
    from finance import whatsapp_out as wout
    monkeypatch.setattr(wout, "enviar_template", lambda c, cid, n, sid, v: {"ok": True, "sid": "SM1"})
    monkeypatch.setenv("TWILIO_TMPL_CONVITE_SID", "HXtest")
    cv.enviar_convite_whatsapp(pool, conv["token"])       # sucesso

    assert cv.listar_historico(pool, conta_id)["total"] == 2
    assert cv.listar_historico(pool, conta_id, somente_falhas=True)["total"] == 1
    assert cv.listar_historico(pool, conta_id, dias=0)["total"] == 0     # fora do período
    assert cv.listar_historico(pool, conta_id, busca="paulo")["total"] == 2
    assert cv.listar_historico(pool, conta_id, busca="não existe")["total"] == 0


def test_reenviar_historico_convite_tenta_de_novo_agora(pool, conta_id, monkeypatch):
    ev = _evento(pool, conta_id, titulo="Reunião Y")
    conv = cv.criar_convidado(pool, conta_id, ev["id"], "Bia", "86988880000")
    monkeypatch.delenv("TWILIO_TMPL_CONVITE_SID", raising=False)
    cv.enviar_convite_whatsapp(pool, conv["token"])       # falha
    log_id = cv.listar_historico(pool, conta_id)["itens"][0]["id"]

    from finance import whatsapp_out as wout
    monkeypatch.setattr(wout, "enviar_template", lambda c, cid, n, sid, v: {"ok": True, "sid": "SM2"})
    monkeypatch.setenv("TWILIO_TMPL_CONVITE_SID", "HXtest")
    r = cv.reenviar_historico(pool, conta_id, log_id)
    assert r["ok"] is True
    assert cv.listar_historico(pool, conta_id)["total"] == 2   # nova tentativa logada, não sobrescreve


def test_reenviar_historico_lembrete_dono_e_convidado(pool, conta_id, monkeypatch):
    ev = _evento(pool, conta_id, titulo="Daily")
    conv = cv.criar_convidado(pool, conta_id, ev["id"], "Ana", "86988881111")
    cv.responder(pool, conv["token"], "confirmado", canal="whatsapp")
    cv.registrar_mensagem(pool, conta_id, ev["id"], conv["id"], "lembrete", "whatsapp_livre",
                          False, "falha_envio")
    cv.registrar_mensagem(pool, conta_id, ev["id"], None, "lembrete", "telegram",
                          False, "falha_envio")
    itens = cv.listar_historico(pool, conta_id)["itens"]
    log_convidado = next(i for i in itens if i["convidado_nome"] == "Ana")
    log_dono = next(i for i in itens if i["convidado_nome"] is None)

    from finance import whatsapp_out as wout
    monkeypatch.setattr(wout, "enviar", lambda c, cid, n, t: {"ok": True})
    assert cv.reenviar_historico(pool, conta_id, log_convidado["id"])["ok"] is True

    from finance import notificar
    monkeypatch.setattr(notificar, "enviar_para_dono", lambda pool, cid, txt: True)
    assert cv.reenviar_historico(pool, conta_id, log_dono["id"])["ok"] is True


def test_reenviar_historico_tipo_remarcado_nao_suportado(pool, conta_id):
    ev = _evento(pool, conta_id)
    conv = cv.criar_convidado(pool, conta_id, ev["id"], "Zé", "86988882222")
    cv.registrar_mensagem(pool, conta_id, ev["id"], conv["id"], "remarcado", "whatsapp_livre",
                          False, "falha_envio")
    log_id = cv.listar_historico(pool, conta_id)["itens"][0]["id"]
    r = cv.reenviar_historico(pool, conta_id, log_id)
    assert r["ok"] is False and r["erro"] == "tipo_nao_suportado"


def test_lembrete_presencial_leva_mapa_e_calendario(pool, conta_id, monkeypatch):
    """O lembrete é o momento em que a pessoa está saindo de casa — é aí que o
    mapa serve. Antes ele levava só título e horário; mapa/chamada/calendário só
    iam na confirmação, dias antes."""
    _usar_qr(pool, conta_id)                          # QR: texto livre sempre
    from finance import whatsapp_out as wout
    capt = {}
    monkeypatch.setattr(wout, "enviar", lambda c, i, n, t: capt.update(texto=t) or {"ok": True})
    agora = ag.agora_brt()
    ev = ag.criar_evento(pool, conta_id, "Reunião", agora + timedelta(minutes=30),
                         local="Edifício Dom João")
    cv.criar_convidado(pool, conta_id, ev["id"], "Hilderlan", "86994283853")
    g = cv.por_evento(pool, conta_id, [ev["id"]])[ev["id"]][0]
    cv.avisar_convidado_confirmado(pool, conta_id, ev, g, "09:30", 30, agora)
    txt = capt["texto"]
    assert "maps.google.com" in txt or "google.com/maps" in txt
    assert "Edifício Dom João" in txt
    assert "calendar.google.com" in txt
    assert "meet.google.com" not in txt                # presencial não tem chamada


def test_lembrete_online_leva_chamada_e_nao_mapa(pool, conta_id, monkeypatch):
    _usar_qr(pool, conta_id)
    from finance import whatsapp_out as wout
    capt = {}
    monkeypatch.setattr(wout, "enviar", lambda c, i, n, t: capt.update(texto=t) or {"ok": True})
    agora = ag.agora_brt()
    ev = ag.criar_evento(pool, conta_id, "Daily", agora + timedelta(minutes=30),
                         local="Online", link_online="https://meet.google.com/abc-defg-hij")
    cv.criar_convidado(pool, conta_id, ev["id"], "Ana", "86999990000")
    g = cv.por_evento(pool, conta_id, [ev["id"]])[ev["id"]][0]
    cv.avisar_convidado_confirmado(pool, conta_id, ev, g, "09:30", 30, agora)
    txt = capt["texto"]
    assert "meet.google.com/abc-defg-hij" in txt
    assert "google.com/maps" not in txt                # online não tem endereço
    assert "calendar.google.com" in txt


def test_lembrete_template_nunca_manda_variavel_vazia(pool, conta_id, monkeypatch):
    """A Meta recusa template com variável em branco. Como um evento online não
    tem endereço e um presencial não tem link de chamada, o template usa o local
    rotulado ({{4}}) e o token do convite ({{5}}) — nunca vazios nos dois casos."""
    from finance import whatsapp_out as wout
    capt = {}
    monkeypatch.setattr(wout, "enviar_template",
                        lambda c, i, n, sid, v, mmlite=False: capt.update(vars=v) or {"ok": True})
    monkeypatch.setenv("TWILIO_TMPL_LEMBRETE_SID", "HXtest")
    agora = ag.agora_brt()
    for kw in ({"local": "Edifício Dom João"},
               {"local": "Online", "link_online": "https://meet.google.com/x"},
               {}):                                    # sem local nenhum
        ev = ag.criar_evento(pool, conta_id, "Reunião", agora + timedelta(minutes=30), **kw)
        cv.criar_convidado(pool, conta_id, ev["id"], "Zé", "86988887777")
        g = cv.por_evento(pool, conta_id, [ev["id"]])[ev["id"]][0]
        g = dict(g, respondido_em=agora - timedelta(days=3), respondido_canal="web")
        cv.avisar_convidado_confirmado(pool, conta_id, ev, g, "09:30", 30, agora)
        assert set(capt["vars"]) == {"1", "2", "3", "4", "5"}
        assert all(str(v).strip() for v in capt["vars"].values()), capt["vars"]
        assert capt["vars"]["5"] == g["token"]         # botão aponta pro convite certo


def _usar_qr(pool, conta_id):
    with pool.connection() as c:
        c.execute("""insert into canais_config (conta_id, canal, identificador, provedor, ativo)
                     values (%s,'whatsapp',%s,'qr',true)
                     on conflict (conta_id, canal) do update set provedor='qr', ativo=true""",
                  (conta_id, f"qr:{conta_id}"))
        c.commit()


def test_convite_no_qr_sai_como_texto_livre_sem_template(pool, conta_id, monkeypatch):
    """No QR não existe template — antes o convite morria em 'sem_template' e quem
    usa QR nunca disparava convite automático, só o link manual."""
    _usar_qr(pool, conta_id)
    monkeypatch.delenv("TWILIO_TMPL_CONVITE_SID", raising=False)
    ev = _evento(pool, conta_id, titulo="Reunião de fechamento")
    conv = cv.criar_convidado(pool, conta_id, ev["id"], "Bia", "86988880002")
    from finance import whatsapp_out as wout
    capt = {}
    monkeypatch.setattr(wout, "enviar", lambda c, i, n, t: capt.update(numero=n, texto=t) or {"ok": True})
    monkeypatch.setattr(wout, "enviar_template", lambda *a, **k: capt.update(template=True) or {"ok": True})
    r = cv.enviar_convite_whatsapp(pool, conv["token"])
    assert r["ok"] is True
    assert capt.get("template") is None                # não tentou template
    assert capt["numero"] == "86988880002"
    assert "Reunião de fechamento" in capt["texto"] and "/convite/" in capt["texto"]
    # e o histórico registra como texto livre, não template
    it = cv.listar_historico(pool, conta_id)["itens"][0]
    assert it["tipo"] == "convite" and it["canal"] == "whatsapp_livre" and it["ok"] is True


def test_botao_de_disparo_aparece_no_qr_sem_template(pool, conta_id, monkeypatch):
    """auto_on (que mostra o botão 📲 Zaq) não pode exigir template de quem usa QR."""
    monkeypatch.delenv("TWILIO_TMPL_CONVITE_SID", raising=False)
    assert cv.template_configurado(pool, conta_id) is False   # twilio sem SID: escondido
    _usar_qr(pool, conta_id)
    assert cv.template_configurado(pool, conta_id) is True     # QR: pode disparar


def test_convite_no_twilio_continua_exigindo_template(pool, conta_id, monkeypatch):
    """Não regride: fora do QR, sem SID, continua sendo 'sem_template'."""
    monkeypatch.delenv("TWILIO_TMPL_CONVITE_SID", raising=False)
    _salvar_tmpl(pool, conta_id, convite=None)                # provedor twilio, sem SID
    ev = _evento(pool, conta_id)
    conv = cv.criar_convidado(pool, conta_id, ev["id"], "Léo", "86988883333")
    assert cv.enviar_convite_whatsapp(pool, conv["token"])["erro"] == "sem_template"


def test_remarcado_no_qr_manda_livre_mesmo_fora_da_janela(pool, conta_id, monkeypatch):
    """No QR não existe janela de 24h nem template: mesmo quem nunca respondeu
    (ou respondeu pela web) recebe o aviso de remarcação como texto livre, em vez
    de cair no template de convite que o QR nem consegue disparar."""
    _usar_qr(pool, conta_id)
    ev = _evento(pool, conta_id, titulo="Alinhamento")
    ca = cv.criar_convidado(pool, conta_id, ev["id"], "Bia", "86988880002")   # nunca respondeu
    from finance import whatsapp_out as wout
    capt = {}
    monkeypatch.setattr(wout, "enviar", lambda c, cid, numero, texto: capt.update(
        numero=numero, texto=texto) or {"ok": True})
    monkeypatch.setattr(wout, "enviar_template", lambda *a, **k: capt.update(template=True) or {"ok": True})
    novo = ag.agora_brt() + timedelta(days=3)
    r = cv.remarcar_e_avisar(pool, conta_id, ev["id"], novo, None, avisar=True, agora=ag.agora_brt())
    assert r["ok"] is True and r["avisados"] == 1
    assert capt.get("template") is None                  # não tentou template
    assert capt["numero"] == "86988880002" and "mudou de horário" in capt["texto"].lower()


def test_listar_fila_mostra_dono_e_convidados_pendentes(pool, conta_id):
    ag.salvar_config(pool, conta_id, resumo_ativo=False, hora_resumo=7, aviso_antes_min=30,
                     avisar_convidados=True)
    agora = ag.agora_brt()
    ev = ag.criar_evento(pool, conta_id, "Consultoria", agora + timedelta(hours=2))
    conv = cv.criar_convidado(pool, conta_id, ev["id"], "Hilderlan", "86994283853")
    cv.responder(pool, conv["token"], "confirmado")
    fila = cv.listar_fila(pool, conta_id, agora)
    assert len(fila) == 2                                     # dono + Hilderlan
    assert {f["convidado_nome"] for f in fila} == {None, "Hilderlan"}
    assert all(f["evento_titulo"] == "Consultoria" for f in fila)
    assert fila[0]["sai_em"] == ev["inicio"] - timedelta(minutes=30)


def test_listar_fila_exclui_quem_ja_foi_avisado(pool, conta_id):
    ag.salvar_config(pool, conta_id, resumo_ativo=False, hora_resumo=7, aviso_antes_min=30)
    agora = ag.agora_brt()
    ev = ag.criar_evento(pool, conta_id, "Já avisado", agora + timedelta(minutes=20))
    with pool.connection() as c:
        c.execute("insert into lembretes_enviados (conta_id, tipo, chave) values (%s,'aviso',%s)",
                  (conta_id, f"evt:{ev['id']}"))
        c.commit()
    assert cv.listar_fila(pool, conta_id, agora) == []


def test_listar_fila_vazia_sem_aviso_configurado(pool, conta_id):
    ag.salvar_config(pool, conta_id, resumo_ativo=False, hora_resumo=7, aviso_antes_min=None)
    ag.criar_evento(pool, conta_id, "Sem aviso", ag.agora_brt() + timedelta(hours=1))
    assert cv.listar_fila(pool, conta_id, ag.agora_brt()) == []


def test_listar_fila_ignora_convidado_nao_confirmado(pool, conta_id):
    ag.salvar_config(pool, conta_id, resumo_ativo=False, hora_resumo=7, aviso_antes_min=30)
    agora = ag.agora_brt()
    ev = ag.criar_evento(pool, conta_id, "Pendente ainda", agora + timedelta(hours=1))
    cv.criar_convidado(pool, conta_id, ev["id"], "Zé", "86988889999")   # nunca confirmou
    fila = cv.listar_fila(pool, conta_id, agora)
    assert len(fila) == 1 and fila[0]["convidado_nome"] is None         # só o dono


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
    # sem canal configurado -> NÃO envia (nunca pelo número global do Zaq, que
    # misturaria a campanha de uma conta com a identidade de outra empresa)
    monkeypatch.setenv("TWILIO_WHATSAPP_FROM", "whatsapp:+5586990000000")
    capt.clear()
    r_sem = wout.enviar_template(_FakeCur(None), 1, "86999", "HXx", {"1": "a", "2": "b"})
    assert r_sem == {"ok": False, "erro": "sem_numero_empresa"}
    assert capt == {}   # nunca chamou o adaptador Twilio
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


# ---- remarcar: muda data mantendo convidados/link -----------------------------

def test_remarcar_e_avisar_muda_data_e_reseta_status(pool, conta_id):
    ev = _evento(pool, conta_id, titulo="Reunião de fechamento")
    ca = cv.criar_convidado(pool, conta_id, ev["id"], "Ana", "86988880001")
    cv.responder(pool, ca["token"], "confirmado")
    novo = ag.agora_brt() + timedelta(days=5)
    r = cv.remarcar_e_avisar(pool, conta_id, ev["id"], novo, None, avisar=False, agora=ag.agora_brt())
    assert r["ok"] is True and r["avisados"] == 0 and r["total_convidados"] == 1
    ev2 = ag.evento_por_id(pool, conta_id, ev["id"])
    assert ev2["inicio"] == novo
    g = cv.por_evento(pool, conta_id, [ev["id"]])[ev["id"]][0]
    assert g["status"] == "pendente"                     # confirmação antiga não vale mais


def test_remarcar_e_avisar_notifica_dentro_da_janela_com_texto_livre(pool, conta_id, monkeypatch):
    ev = _evento(pool, conta_id, titulo="Alinhamento")
    ca = cv.criar_convidado(pool, conta_id, ev["id"], "Bia", "86988880002")
    # canal="whatsapp": só uma confirmação de verdade pelo WhatsApp abre a janela de 24h.
    cv.responder(pool, ca["token"], "confirmado", canal="whatsapp")   # respondido_em = agora (dentro da janela)
    from finance import whatsapp_out as wout
    capt = {}
    monkeypatch.setattr(wout, "enviar", lambda c, cid, numero, texto: capt.update(
        conta_id=cid, numero=numero, texto=texto) or {"ok": True})
    novo = ag.agora_brt() + timedelta(days=3)
    r = cv.remarcar_e_avisar(pool, conta_id, ev["id"], novo, None, avisar=True, agora=ag.agora_brt())
    assert r["ok"] is True and r["avisados"] == 1
    assert capt["numero"] == "86988880002"
    assert "mudou de horário" in capt["texto"].lower()
    assert "/convite/" in capt["texto"]                   # mesmo link, não convite novo


def test_remarcar_e_avisar_notifica_com_link_da_chamada(pool, conta_id, monkeypatch):
    ev = ag.criar_evento(pool, conta_id, "Daily remarcada", ag.agora_brt() + timedelta(days=1),
                         local="Online", link_online="https://meet.google.com/abc-defg-hij")
    ca = cv.criar_convidado(pool, conta_id, ev["id"], "Bia", "86988880002")
    cv.responder(pool, ca["token"], "confirmado", canal="whatsapp")
    from finance import whatsapp_out as wout
    capt = {}
    monkeypatch.setattr(wout, "enviar", lambda c, cid, numero, texto: capt.update(texto=texto) or {"ok": True})
    novo = ag.agora_brt() + timedelta(days=3)
    cv.remarcar_e_avisar(pool, conta_id, ev["id"], novo, None, avisar=True, agora=ag.agora_brt())
    assert "meet.google.com/abc-defg-hij" in capt["texto"]


def test_remarcar_e_avisar_fora_da_janela_usa_template_de_convite(pool, conta_id, monkeypatch):
    ev = _evento(pool, conta_id, titulo="Kickoff")
    cv.criar_convidado(pool, conta_id, ev["id"], "Léo", "86988880003")  # nunca respondeu -> sem janela
    from finance import whatsapp_out as wout
    capt = {}
    monkeypatch.setattr(wout, "enviar_template", lambda c, cid, numero, sid, variaveis: capt.update(
        sid=sid, numero=numero, vars=variaveis) or {"ok": True, "sid": "SM9"})
    monkeypatch.setenv("TWILIO_TMPL_CONVITE_SID", "HXtest")
    novo = ag.agora_brt() + timedelta(days=2)
    r = cv.remarcar_e_avisar(pool, conta_id, ev["id"], novo, None, avisar=True, agora=ag.agora_brt())
    assert r["ok"] is True and r["avisados"] == 1
    assert capt["sid"] == "HXtest" and capt["numero"] == "86988880003"


def test_remarcar_e_avisar_reaproveita_evento_cancelado(pool, conta_id):
    """'reaproveitar' na caixa do dia é o MESMO fluxo do remarcar normal, só que
    o evento alvo já está cancelado — remarcar reativa."""
    ev = _evento(pool, conta_id, titulo="Reunião cancelada")
    assert ag.cancelar_evento(pool, conta_id, ev["id"]) is True
    assert ag.evento_por_id(pool, conta_id, ev["id"]) is None
    novo = ag.agora_brt() + timedelta(days=4)
    r = cv.remarcar_e_avisar(pool, conta_id, ev["id"], novo, None, avisar=False, agora=ag.agora_brt())
    assert r["ok"] is True
    ev2 = ag.evento_por_id(pool, conta_id, ev["id"])
    assert ev2 is not None and ev2["inicio"] == novo   # ativo de novo, na nova data


def test_remarcar_e_avisar_evento_inexistente_ou_de_outra_conta(pool, conta_id):
    assert cv.remarcar_e_avisar(pool, conta_id, 999999, ag.agora_brt(), None,
                                avisar=False, agora=ag.agora_brt())["ok"] is False
    with pool.connection() as c:
        outra = c.execute("insert into contas (tipo, nome) values ('pj','Outra') returning id").fetchone()[0]
        c.commit()
    ev = _evento(pool, conta_id)
    assert cv.remarcar_e_avisar(pool, outra, ev["id"], ag.agora_brt(), None,
                                avisar=False, agora=ag.agora_brt())["ok"] is False

# ---------------------------------------------------------------- nome comercial
# O convidado vê este nome em três lugares: título da página pública, o "A {empresa}
# quer marcar com você" e o assunto do convite por WhatsApp/e-mail. `contas.nome` é
# quem ABRIU a conta — o convite da Prime Eventos chegava assinado "MANOEL SOARES",
# nome que o cliente do salão não tem como reconhecer.

def _conta(pool, **campos):
    cols = ", ".join(campos)
    marks = ", ".join(["%s"] * len(campos))
    with pool.connection() as c:
        cid = c.execute(f"insert into contas (tipo, {cols}) values ('pj', {marks}) returning id",
                        tuple(campos.values())).fetchone()[0]
        c.commit()
    return cid


def test_empresa_usa_nome_fantasia_e_nao_o_titular(pool):
    cid = _conta(pool, nome="MANOEL SOARES", nome_fantasia="PRIME EVENTOS",
                 razao_social="PRIME EVENTOS LTDA")
    conv = cv.criar_convidado(pool, cid, _evento(pool, cid)["id"], "Jacqueline", "5586999990001")
    assert cv.por_token(pool, conv["token"])["empresa"] == "PRIME EVENTOS"


def test_sem_fantasia_cai_na_razao_social(pool):
    cid = _conta(pool, nome="Rawilson Osternes", razao_social="RAMO ESTRATEGIA E CAPITAL")
    conv = cv.criar_convidado(pool, cid, _evento(pool, cid)["id"], "Cliente", "5586999990002")
    assert cv.por_token(pool, conv["token"])["empresa"] == "RAMO ESTRATEGIA E CAPITAL"


def test_sem_fantasia_nem_razao_cai_no_nome(pool):
    # conta PF, ou PJ que ainda não preencheu os dados da empresa: aí o nome da
    # pessoa É o nome comercial, e trocar por vazio seria pior que o problema
    cid = _conta(pool, nome="Juliana Teixeira de Oliveira")
    conv = cv.criar_convidado(pool, cid, _evento(pool, cid)["id"], "Cliente", "5586999990003")
    assert cv.por_token(pool, conv["token"])["empresa"] == "Juliana Teixeira de Oliveira"


def test_fantasia_so_com_espacos_nao_apaga_o_nome(pool):
    # o cadastro deixa salvar " " no campo; sem o nullif(btrim(...)) o convite
    # chegaria assinado com uma string em branco
    cid = _conta(pool, nome="Paulo Costa", nome_fantasia="   ", razao_social="")
    conv = cv.criar_convidado(pool, cid, _evento(pool, cid)["id"], "Cliente", "5586999990004")
    assert cv.por_token(pool, conv["token"])["empresa"] == "Paulo Costa"


def test_titulo_do_convite_leva_o_nome_comercial(pool):
    # o mesmo dado alimenta o assunto do WhatsApp/e-mail (_titulo_com_extras)
    cid = _conta(pool, nome="MANOEL SOARES", nome_fantasia="PRIME EVENTOS")
    ev = _evento(pool, cid, titulo="VISITA TÉCNICA - PEDRO")
    conv = cv.criar_convidado(pool, cid, ev["id"], "Jacqueline", "5586999990005")
    titulo = cv._titulo_com_extras(pool, cv.por_token(pool, conv["token"]))
    assert "PRIME EVENTOS" in titulo and "MANOEL SOARES" not in titulo


# ------------------------------- o RSVP não pode ser desfeito pelo que vem depois

def _bloco_rsvp() -> str:
    """O trecho do webhook do Twilio que intercepta a resposta do convidado."""
    import inspect
    from web import painel_prospeccao as pp
    fonte = inspect.getsource(pp.webhook_twilio)
    i = fonte.index("RSVP de convite")
    return fonte[i:fonte.index("Botões do template de 1º contato")]


def test_o_pos_resposta_nao_derruba_a_rota():
    """O que quebrou em 23/08. `responder()` grava o RSVP; o que vem depois é
    cortesia — avisar o dono e agradecer ao convidado. Uma exceção ali devolvia 500
    pro Twilio, que REENTREGA a mensagem; na reentrega o convite já não estava
    'pendente', `pendentes_por_numero` voltava vazio, o fluxo caía no inbox e a IA
    respondia "não tenho nada registrado aqui sobre um evento" — pra quem tinha
    acabado de confirmar."""
    bloco = _bloco_rsvp()
    assert "try:" in bloco, "o pós-resposta precisa estar protegido"
    assert "except Exception" in bloco

    # O return fica FORA do try, e quem prova isso é a INDENTAÇÃO — não a ordem no
    # texto. Um `return` dentro do `except` também aparece depois dele e passaria
    # numa comparação de posição, mas aí o caminho FELIZ não retorna: cai no bloco
    # da prospecção e depois no inbox, que é a IA respondendo de novo.
    def _recuo(linha: str) -> int:
        return len(linha) - len(linha.lstrip())

    linhas = [l for l in bloco.splitlines() if l.strip()]
    recuo_try = next(_recuo(l) for l in linhas if l.strip() == "try:")
    recuo_ret = next(_recuo(l) for l in linhas if l.strip().startswith("return Response"))
    assert recuo_ret == recuo_try, (
        f"o return está recuado {recuo_ret} e o try {recuo_try}: ele precisa estar no "
        "MESMO nível do try, senão o caminho feliz não retorna e a IA responde depois")


def test_o_rsvp_nao_usa_a_conversa_do_inbox():
    """`conv_id` é do inbox, atribuído bem mais abaixo. Usá-lo aqui era o
    UnboundLocalError que derrubava a rota — e o convidado não é necessariamente um
    lead, então a conversa dele pode nem existir."""
    bloco = _bloco_rsvp()
    assert "conta_id, conv_id)" not in bloco, "voltou a usar o conv_id do inbox"
    assert "_conversa_wa_do_contato" in bloco, \
        "o chip tem que sair da conversa DO CONTATO, quando ela existir"


def test_confirmar_presenca_e_reconhecido_como_rsvp():
    """O rótulo exato do botão do template, como ele volta do WhatsApp."""
    assert cv.rsvp_por_texto("Confirmar Presença") == "confirmado"
    assert cv.rsvp_por_texto("Não vou poder") == "recusado"
    assert cv.rsvp_por_texto("Remarcar") == "remarcar"


def test_texto_qualquer_nao_vira_rsvp():
    """Se virasse, uma conversa comum seria engolida pelo caminho do convite."""
    for t in ("oi", "quanto custa?", "bom dia", ""):
        assert cv.rsvp_por_texto(t) is None
