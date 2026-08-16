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


def test_frase_nomes():
    assert ag.frase_nomes([]) == ""
    assert ag.frase_nomes(["Ana"]) == "Ana"
    assert ag.frase_nomes(["Ana", "Carlos"]) == "Ana e Carlos"
    assert ag.frase_nomes(["Ana", "Carlos", "Bia"]) == "Ana, Carlos e Bia"


def test_link_maps_usa_o_local():
    url = ag.link_maps("Rua das Flores, 123 - Centro")
    assert url.startswith("https://www.google.com/maps/search/?api=1&query=")
    assert "Rua" in url


# ---------- caixa do dia: convidados embutidos (puro) ----------

def test_eventos_por_dia_embute_convidados_com_link_de_whatsapp():
    from web import painel_agenda as pa
    eventos = [_ev(id=1, titulo="Reunião com Rosana", tipo="empresa")]
    convidados = {1: [
        {"nome": "Rosana", "contato": "86 99123-4567", "status": "confirmado", "status_rot": "Confirmado"},
    ]}
    dia = pa._eventos_por_dia(eventos, convidados)
    g = dia["2026-07-28"]["eventos"][0]["convidados"][0]
    assert g["nome"] == "Rosana" and g["status"] == "confirmado"
    assert g["wa"].startswith("https://wa.me/5586991234567?text=")
    assert "Rosana" in g["wa"]


def test_eventos_por_dia_convidado_sem_contato_nao_gera_link():
    from web import painel_agenda as pa
    eventos = [_ev(id=1, tipo="empresa")]
    convidados = {1: [{"nome": "SemZap", "contato": "", "status": "pendente", "status_rot": "Pendente"}]}
    dia = pa._eventos_por_dia(eventos, convidados)
    g = dia["2026-07-28"]["eventos"][0]["convidados"][0]
    assert g["wa"] == ""


def test_eventos_por_dia_sem_convidados_e_lista_vazia():
    from web import painel_agenda as pa
    dia = pa._eventos_por_dia([_ev(id=1, tipo="empresa")])
    assert dia["2026-07-28"]["eventos"][0]["convidados"] == []


def test_link_maps_sem_local_volta_none():
    assert ag.link_maps("") is None
    assert ag.link_maps(None) is None
    assert ag.link_maps("   ") is None


def test_link_maps_reuniao_online_volta_none():
    """local="Online" (botão dedicado no form) não é endereço nenhum — nunca
    deve virar link de mapa, senão manda um link sem sentido pro convidado."""
    assert ag.link_maps("Online") is None
    assert ag.link_maps("online") is None
    assert ag.link_maps("  ONLINE  ") is None


def test_eh_online():
    assert ag.eh_online("Online") is True
    assert ag.eh_online("online") is True
    assert ag.eh_online("Rua das Flores, 123") is False
    assert ag.eh_online("") is False
    assert ag.eh_online(None) is False


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
    migr = Path(__file__).resolve().parent.parent / "db" / "migracoes"
    with p.connection() as c:
        for nome in ("098_agenda.sql", "099_agenda_tipo.sql", "100_evento_convidados.sql",
                    "101_agenda_lembretes.sql", "126_agenda_avisar_convidados.sql",
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


def test_criar_evento_com_link_online(pool, conta_id):
    ev = ag.criar_evento(pool, conta_id, "Daily", ag.agora_brt() + timedelta(days=1),
                         local="Online", link_online="https://meet.google.com/abc-defg-hij")
    assert ev["link_online"] == "https://meet.google.com/abc-defg-hij"
    assert ag.evento_por_id(pool, conta_id, ev["id"])["link_online"] == "https://meet.google.com/abc-defg-hij"


def test_criar_evento_sem_link_online_fica_none(pool, conta_id):
    ev = ag.criar_evento(pool, conta_id, "Reunião física", ag.agora_brt() + timedelta(days=1), local="Escritório")
    assert ev["link_online"] is None


def test_isolamento_por_conta(pool, conta_id):
    outra = None
    with pool.connection() as c:
        outra = c.execute("insert into contas (tipo,nome) values ('pf','Outra') returning id").fetchone()[0]
        c.commit()
    ag.criar_evento(pool, conta_id, "So minha", ag.agora_brt() + timedelta(days=1))
    assert ag.proximos(pool, outra) == []          # a outra conta não vê


# ---------- desfecho (aconteceu/não aconteceu) + reaproveitar não realizados ----------

def test_remarcar_limpa_desfecho_marcado(pool, conta_id):
    ev = ag.criar_evento(pool, conta_id, "Follow-up", ag.agora_brt() - timedelta(days=2))
    assert ag.marcar_desfecho(pool, conta_id, ev["id"], "nao_realizado", ag.agora_brt())
    novo = ag.agora_brt() + timedelta(days=3)
    assert ag.remarcar_evento(pool, conta_id, ev["id"], novo) is True
    ev2 = ag.evento_por_id(pool, conta_id, ev["id"])
    assert ev2["inicio"] == novo and ev2["desfecho"] is None       # desfecho antigo não vale mais


def test_remarcar_reativa_evento_cancelado(pool, conta_id):
    ev = ag.criar_evento(pool, conta_id, "Reunião cancelada", ag.agora_brt() + timedelta(days=1))
    assert ag.cancelar_evento(pool, conta_id, ev["id"]) is True
    assert ag.evento_por_id(pool, conta_id, ev["id"]) is None      # some do "ativo"
    novo = ag.agora_brt() + timedelta(days=5)
    assert ag.remarcar_evento(pool, conta_id, ev["id"], novo) is True
    ev2 = ag.evento_por_id(pool, conta_id, ev["id"])
    assert ev2 is not None and ev2["inicio"] == novo               # ativo de novo


def test_marcar_desfecho_so_pro_passado(pool, conta_id):
    passado = ag.criar_evento(pool, conta_id, "Ontem", ag.agora_brt() - timedelta(days=1))
    futuro = ag.criar_evento(pool, conta_id, "Amanhã", ag.agora_brt() + timedelta(days=1))
    agora = ag.agora_brt()
    assert ag.marcar_desfecho(pool, conta_id, passado["id"], "realizado", agora) is True
    assert ag.marcar_desfecho(pool, conta_id, futuro["id"], "realizado", agora) is False   # ainda não aconteceu
    assert ag.marcar_desfecho(pool, conta_id, passado["id"], "valor_invalido", agora) is False


def test_eventos_para_reaproveitar_so_traz_nao_realizado(pool, conta_id):
    """Cancelado fica de FORA de propósito — quem cancela já sabe que não tem
    previsão, então listar cancelado só lotaria a lista de sugestão à toa."""
    agora = ag.agora_brt()
    cancelado = ag.criar_evento(pool, conta_id, "Cancelado recente", agora - timedelta(days=3))
    ag.cancelar_evento(pool, conta_id, cancelado["id"])
    nao_realizado = ag.criar_evento(pool, conta_id, "Não rolou", agora - timedelta(days=1))
    ag.marcar_desfecho(pool, conta_id, nao_realizado["id"], "nao_realizado", agora)
    realizado = ag.criar_evento(pool, conta_id, "Aconteceu normal", agora - timedelta(days=1))
    ag.marcar_desfecho(pool, conta_id, realizado["id"], "realizado", agora)
    ag.criar_evento(pool, conta_id, "Futuro qualquer", agora + timedelta(days=1))

    r = ag.eventos_para_reaproveitar(pool, conta_id, agora)
    ids = {e["id"] for e in r}
    assert ids == {nao_realizado["id"]}                             # só o não realizado


def test_eventos_para_reaproveitar_ignora_antigos_demais(pool, conta_id):
    agora = ag.agora_brt()
    velho = ag.criar_evento(pool, conta_id, "Muito antigo", agora - timedelta(days=200))
    ag.marcar_desfecho(pool, conta_id, velho["id"], "nao_realizado", agora)
    r = ag.eventos_para_reaproveitar(pool, conta_id, agora)
    assert velho["id"] not in {e["id"] for e in r}


def test_achar_por_titulo_passado(pool, conta_id):
    agora = ag.agora_brt()
    ev = ag.criar_evento(pool, conta_id, "Reunião com Paulo", agora - timedelta(days=2))
    ag.criar_evento(pool, conta_id, "Reunião com Paulo futura", agora + timedelta(days=2))
    cand = ag.achar_por_titulo_passado(pool, conta_id, "paulo", agora)
    assert [c["id"] for c in cand] == [ev["id"]]                   # só o passado, não o futuro


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


def test_ferramenta_marcar_desfecho_por_titulo(pool, conta_id):
    ev = ag.criar_evento(pool, conta_id, "Reunião com o Paulo", ag.agora_brt() - timedelta(days=1))
    ferrs = {f.nome: f for f in construir_ferramentas_agenda(pool, conta_id)}
    r = ferrs["marcar_desfecho_evento"].executar({"titulo": "Paulo", "aconteceu": "nao"})
    assert "não aconteceu" in r.lower()
    assert ag.evento_por_id(pool, conta_id, ev["id"])["desfecho"] == "nao_realizado"


def test_ferramenta_marcar_desfecho_recusa_evento_futuro(pool, conta_id):
    ag.criar_evento(pool, conta_id, "Reunião de amanhã com a Bia", ag.agora_brt() + timedelta(days=1))
    ferrs = {f.nome: f for f in construir_ferramentas_agenda(pool, conta_id)}
    r = ferrs["marcar_desfecho_evento"].executar({"titulo": "Bia", "aconteceu": "sim"})
    assert "não achei" in r.lower()   # achar_por_titulo_passado não encontra evento futuro


def test_ferramenta_marcar_desfecho_pede_esclarecimento(pool, conta_id):
    ferrs = {f.nome: f for f in construir_ferramentas_agenda(pool, conta_id)}
    r = ferrs["marcar_desfecho_evento"].executar({"titulo": "qualquer", "aconteceu": "talvez"})
    assert "aconteceu ou não aconteceu" in r.lower()


# ---------- portal: tipo, mês, config do lembrete e feed .ics (banco) ----------

def test_tipo_persiste_e_default(pool, conta_id):
    amanha = ag.agora_brt() + timedelta(days=1)
    ev = ag.criar_evento(pool, conta_id, "Reunião", amanha, tipo="empresa")
    assert ev["tipo"] == "empresa"
    ev2 = ag.criar_evento(pool, conta_id, "Consulta", amanha)   # default
    assert ev2["tipo"] == "pessoal"
    ev3 = ag.criar_evento(pool, conta_id, "X", amanha, tipo="invalido")  # sanitiza
    assert ev3["tipo"] == "pessoal"


def test_eventos_mes_pega_so_o_mes(pool, conta_id):
    dentro = ag.agora_brt().replace(year=2099, month=3, day=15, hour=10, minute=0,
                                    second=0, microsecond=0)
    fora = dentro.replace(month=4, day=2)
    ag.criar_evento(pool, conta_id, "No mês", dentro)
    ag.criar_evento(pool, conta_id, "Mês seguinte", fora)
    do_mes = ag.eventos_mes(pool, conta_id, 2099, 3)
    titulos = {e["titulo"] for e in do_mes}
    assert "No mês" in titulos and "Mês seguinte" not in titulos


def test_config_lembrete_default_e_salvar(pool, conta_id):
    cfg = ag.get_config(pool, conta_id)          # sem linha -> defaults
    assert cfg["resumo_ativo"] is False and cfg["hora_resumo"] == 7
    ag.salvar_config(pool, conta_id, resumo_ativo=True, hora_resumo=8, aviso_antes_min=30)
    cfg = ag.get_config(pool, conta_id)
    assert cfg["resumo_ativo"] is True and cfg["hora_resumo"] == 8 and cfg["aviso_antes_min"] == 30
    assert cfg["lembrete_ativo"] is True          # derivado
    # só aviso ligado -> resumo fica desligado (não manda resumo)
    ag.salvar_config(pool, conta_id, resumo_ativo=False, hora_resumo=7, aviso_antes_min=15)
    cfg = ag.get_config(pool, conta_id)
    assert cfg["resumo_ativo"] is False and cfg["aviso_antes_min"] == 15 and cfg["lembrete_ativo"] is True
    # tudo desligado
    ag.salvar_config(pool, conta_id, resumo_ativo=False, hora_resumo=7, aviso_antes_min=None)
    cfg = ag.get_config(pool, conta_id)
    assert cfg["lembrete_ativo"] is False and cfg["aviso_antes_min"] is None


def test_feed_token_idempotente_e_resolve(pool, conta_id):
    t1 = ag.garantir_feed_token(pool, conta_id)
    t2 = ag.garantir_feed_token(pool, conta_id)   # não gera outro
    assert t1 and t1 == t2
    assert ag.conta_por_feed_token(pool, t1) == conta_id
    assert ag.conta_por_feed_token(pool, "nao-existe") is None
    # salvar_config depois NÃO apaga o token (upsert preserva)
    ag.salvar_config(pool, conta_id, resumo_ativo=True, hora_resumo=7, aviso_antes_min=None)
    assert ag.get_config(pool, conta_id)["feed_token"] == t1


def test_feed_ics_isolado_por_conta(pool, conta_id):
    ag.criar_evento(pool, conta_id, "Meu evento feed", ag.agora_brt() + timedelta(days=2))
    ics = ag.feed_ics(ag.eventos_para_feed(pool, conta_id))
    assert "Meu evento feed" in ics and ics.startswith("BEGIN:VCALENDAR")


def test_sugerir_por_titulo(pool, conta_id):
    ag.criar_evento(pool, conta_id, "Reunião com Mailson - Sistema Zaq",
                    ag.agora_brt() + timedelta(days=1), tipo="empresa")
    ag.criar_evento(pool, conta_id, "Consulta médica",
                    ag.agora_brt() + timedelta(days=2), tipo="pessoal")
    # termo com palavra em comum ("sistema") -> a reunião do Mailson vem primeiro
    sug = ag.sugerir_por_titulo(pool, conta_id, "reunião sistema alinhamento")
    assert sug and "Mailson" in sug[0]["titulo"]
    # termo sem nada a ver ainda devolve algo (nunca dá dead-end)
    assert ag.sugerir_por_titulo(pool, conta_id, "xyzabc")
    # agenda vazia -> lista vazia
    with pool.connection() as c:
        outra = c.execute("insert into contas (tipo,nome) values ('pf','Nova') returning id").fetchone()[0]
        c.commit()
    assert ag.sugerir_por_titulo(pool, outra, "qualquer") == []


def test_resolver_sugere_quando_nao_bate(pool, conta_id):
    ag.criar_evento(pool, conta_id, "Reunião com Mailson - Sistema Zaq",
                    ag.agora_brt() + timedelta(days=1), tipo="empresa")
    ferrs = {f.nome: f for f in construir_ferramentas_agenda(pool, conta_id)}
    # nome mangado pela voz -> em vez de "não achei", sugere o que tem na agenda
    r = ferrs["cancelar_evento"].executar({"titulo": "alinhamento smartcenter"})
    assert "Mailson" in r and "agenda tem" in r
