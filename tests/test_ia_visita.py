"""A IA da regra por número MARCANDO a visita (migração 390, finance/ia_visita.py).

O que cada bloco segura:

* A GRADE é a do dono: meia hora cabe na hora cheia de antes; 12h–14h e domingo não.
* ANTES DE MARCAR confere tudo: antecedência, outra visita (com a folga), a festa do
  dia (nada nas 3h antes; 19h só em dia sem festa) e o que foi combinado nas
  conversas e não está na agenda.
* MARCA SOB TRAVA e pelo caminho de sempre (`cockpit.agendar_visita`): a visita conta
  pro dono do lead (o zaq teste), é anotada como da IA, e a anfitriã é avisada.
  Remarcar move a MESMA visita, até 2 vezes.
* A LETRA do cliente vira o horário oferecido, sem depender da IA ler certo.
* O RELÓGIO: véspera às 18h, 2h antes, "ninguém confirmou" pra anfitriã, a falta —
  uma vez cada, e pelo chip da conversa.
* A confirmação do painel também sai pelo chip da conversa do lead (era o principal).
"""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from finance import agenda as ag
from finance import agente
from finance import chip_regra as cr
from finance import cockpit as ck
from finance import ia_visita as iv

BASE = Path(__file__).resolve().parents[1] / "db" / "migracoes"
BRT = ag.BRT
EMPRESA, CHIP2 = 34, 36
NUM = "558699990001"
# quarta-feira 30/09/2026, 10h (Brasília)
QUA_10 = datetime(2026, 9, 30, 10, 0, tzinfo=BRT)

_SQL = """
create table nichos (id bigserial primary key, slug text, nome text);
create table contas (id bigint primary key, nome text, chip_de bigint, nicho_id bigint,
  nome_fantasia text, razao_social text, endereco text, bairro text, cidade text, uf text);
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text, ativo boolean default true, whatsapp text);
create table prospeccao (id bigserial primary key, conta_id bigint, vendedor_id bigint,
  empresa text, contato text, whatsapp text, telefone text, status text default 'novo',
  evento_tipo text, evento_convidados int, segmento text, cidade text, uf text,
  ultimo_contato_em timestamptz,
  atualizado_em timestamptz default now(), criado_em timestamptz default now());
create table prospeccao_atividades (id bigserial primary key, prospeccao_id bigint,
  membro_id bigint, tipo text not null, resultado text, descricao text not null,
  agendado_para timestamptz, criado_em timestamptz not null default now());
create table eventos_agenda (id bigserial primary key, conta_id bigint, membro_id bigint,
  titulo text, inicio timestamptz, fim timestamptz, local text, descricao text,
  status text default 'ativo', tipo text default 'pessoal', lembrete_min int,
  link_online text, prospeccao_id bigint, cliente_id bigint, ics_token text,
  pre_reserva_ate timestamptz, sinal_centavos bigint, tipo_evento text, convidados int,
  hora_sugerida boolean default false, ocupa_espaco boolean, desfecho text, marcado_por text,
  criado_em timestamptz default now());
create table funil_etapas (id bigserial primary key, conta_id bigint, chave text, rotulo text,
  ordem int default 0, fase text not null default 'venda');
create table conversas (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  canal text default 'whatsapp', contato_ref text, contato_nome text,
  status text default 'aberta', agente_ativo boolean default true,
  responsavel_membro_id bigint, ultima_msg_em timestamptz default now(), chip_id bigint);
create table mensagens (id bigserial primary key, conversa_id bigint, canal text,
  direcao text, autor text, texto text, membro_id bigint, provider_sid text,
  status text, criado_em timestamptz default now());
create unique index idx_mensagens_sid_conversa
  on mensagens (conversa_id, provider_sid) where provider_sid is not null;
"""


@pytest.fixture()
def pool(monkeypatch):
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_ia_visita_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity "
                  "where datname=%s and pid <> pg_backend_pid()", (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=2, max_size=6, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        c.execute("insert into nichos (slug, nome) values ('eventos','Eventos')")
        c.execute("""insert into contas (id, nome, chip_de, nicho_id, nome_fantasia, endereco, cidade, uf)
                     values (%s,'Prime',null,1,'Prime Eventos','Rua A, 10','Teresina','PI'),
                            (%s,'CP Thiago',%s,null,null,null,null,null)""",
                  (EMPRESA, CHIP2, EMPRESA))
        for m in ("388_regra_por_chip.sql", "390_ia_marca_visita.sql"):
            c.execute((BASE / m).read_text(encoding="utf-8"))
        c.commit()
    # o cadastro do cliente e os avisos pro celular não são o assunto aqui
    monkeypatch.setattr(ck, "_cliente_do_lead", lambda *a, **k: None)
    yield p
    p.close()


@pytest.fixture()
def prime(pool, monkeypatch):
    """A regra do CP Thiago: zaq teste com a IA, Jacqueline anfitriã, a IA marca."""
    avisos = []
    monkeypatch.setattr(cr, "notificar", lambda pool_, conta, mid, t, corpo, url:
                        avisos.append((mid, t, corpo)) or True)
    with pool.connection() as c:
        ids = {}
        for n in ("ZAQ", "JACQUELINE", "MANOEL"):
            ids[n] = c.execute("insert into membros (conta_id, nome, email) values (%s,%s,%s) "
                               "returning id", (EMPRESA, n.title(), f"{n.lower()}@x.com")).fetchone()[0]
        assert cr.salvar(c, EMPRESA, CHIP2, {"ativa": True, "membro_id": ids["ZAQ"],
                                             "ia_ligada": True,
                                             "aviso_agenda_membro_id": ids["JACQUELINE"]})["ok"]
        assert iv.salvar(c, EMPRESA, CHIP2, {"visita_marca": True,
                                             "visita_anfitria_id": ids["JACQUELINE"]}) is None
        c.execute("update chip_regra set vale_desde = now() - interval '1 day'")
        c.commit()
        regra = cr.regra(c, EMPRESA, CHIP2)
        cfg = iv.config(c, regra)
    return {"ids": ids, "regra": regra, "cfg": cfg, "avisos": avisos}


def _lead(pool, prime, *, chip=CHIP2, nome="Larissa"):
    with pool.connection() as c:
        lead = c.execute("insert into prospeccao (conta_id, vendedor_id, contato, whatsapp) "
                         "values (%s,%s,%s,%s) returning id",
                         (EMPRESA, prime["ids"]["ZAQ"], nome, "+" + NUM)).fetchone()[0]
        c.execute("insert into chip_regra_leads (prospeccao_id, conta_id, chip_id, membro_id) "
                  "values (%s,%s,%s,%s)", (lead, EMPRESA, CHIP2, prime["ids"]["ZAQ"]))
        conv = c.execute("insert into conversas (conta_id, prospeccao_id, contato_ref, chip_id) "
                         "values (%s,%s,%s,%s) returning id", (EMPRESA, lead, NUM, chip)).fetchone()[0]
        c.commit()
    return lead, conv


def _evento(pool, inicio, *, horas=1, tipo_evento=None, status="ativo", titulo="Compromisso",
            sem_fim=False):
    with pool.connection() as c:
        c.execute("""insert into eventos_agenda (conta_id, titulo, inicio, fim, status, tipo_evento)
                     values (%s,%s,%s,%s,%s,%s)""",
                  (EMPRESA, titulo, inicio, None if sem_fim else inicio + timedelta(hours=horas),
                   status, tipo_evento))
        c.commit()


def _dia(d, h, m=0):
    """Dia de outubro/2026 em Brasília (01/10 é quinta)."""
    return datetime(2026, 10, d, h, m, tzinfo=BRT)


# ══════════════════════════════════════════════ a grade

def test_a_grade_de_fabrica_e_a_meia_hora():
    g = iv.GRADE_PADRAO
    assert iv.estado(g, _dia(1, 9)) == iv.OK             # quinta 9h
    assert iv.estado(g, _dia(1, 9, 30)) == iv.OK         # 9h30 cabe no 9h
    assert iv.estado(g, _dia(1, 9, 15)) is None          # quarto de hora, não
    assert iv.estado(g, _dia(1, 12)) is None             # almoço: 25% vieram
    assert iv.estado(g, _dia(1, 15)) == iv.CONF_DIA
    assert iv.estado(g, _dia(1, 19)) == iv.SEM_FESTA
    assert iv.estado(g, _dia(3, 17)) is None             # sábado à tarde tem festa
    assert iv.estado(g, _dia(4, 10)) is None             # domingo só a pedido
    assert iv.estado(g, _dia(5, 10)) is None             # segunda de manhã, não
    assert iv.estado(g, _dia(5, 17)) == iv.OK


def test_grade_da_tela_so_aceita_estado_e_hora_validos():
    campos = {"g_3_9": "ok", "g_3_12": "conf_dia", "g_5_10": "inventado", "g_6_25": "ok"}
    g = iv.grade_do_form(lambda k: campos.get(k, ""))
    assert g["3"] == {"9": "ok", "12": "conf_dia"} and g["5"] == {} and g["6"] == {}


# ══════════════════════════════════════════════ o que ocupa

def test_antecedencia(pool, prime):
    cfg = prime["cfg"]
    assert iv.cabe(pool, EMPRESA, cfg, _dia(1, 9), QUA_10) == (True, iv.OK)
    assert iv.cabe(pool, EMPRESA, cfg, datetime(2026, 9, 30, 11, tzinfo=BRT), QUA_10)[1] == "cedo"
    assert iv.cabe(pool, EMPRESA, cfg, datetime(2026, 10, 16, 10, tzinfo=BRT), QUA_10)[1] == "longe"


def test_outra_visita_e_a_folga(pool, prime):
    """Visita das 9h dura 60 + 30 de folga: o 10h já está livre, mas um compromisso
    às 10h15 bate na folga da visita das 9h."""
    _evento(pool, _dia(1, 10, 15), horas=1)
    assert iv.cabe(pool, EMPRESA, prime["cfg"], _dia(1, 9), QUA_10) == (False, "ocupado")
    assert iv.cabe(pool, EMPRESA, prime["cfg"], _dia(2, 9), QUA_10)[0]


def test_nada_nas_3_horas_antes_da_festa(pool, prime):
    _evento(pool, _dia(1, 20), tipo_evento="15 anos", sem_fim=True)
    cfg = prime["cfg"]
    assert iv.cabe(pool, EMPRESA, cfg, _dia(1, 17), QUA_10) == (False, "festa")
    assert iv.cabe(pool, EMPRESA, cfg, _dia(1, 18), QUA_10) == (False, "festa")
    assert iv.cabe(pool, EMPRESA, cfg, _dia(1, 11), QUA_10) == (True, iv.OK)


def test_19h_so_em_dia_sem_festa_e_pre_reserva_conta_como_festa(pool, prime):
    cfg = prime["cfg"]
    assert iv.cabe(pool, EMPRESA, cfg, _dia(2, 19), QUA_10) == (True, iv.SEM_FESTA)
    # data SEGURADA (sem tipo) de manhã: o 19h do mesmo dia deixa de valer
    _evento(pool, _dia(2, 9), horas=2, status="pre_reservado", titulo="Casamento")
    assert iv.cabe(pool, EMPRESA, cfg, _dia(2, 19), QUA_10) == (False, "festa")


def test_o_que_foi_combinado_na_conversa_de_outro_cliente(pool, prime):
    lead, conv = _lead(pool, prime)
    _, outra = _lead(pool, prime, nome="Ana")
    with pool.connection() as c:
        c.execute("""insert into mensagens (conversa_id, direcao, autor, texto)
                     values (%s,'out','humano','Fechado então, visita dia 1/10 às 17h!')""", (outra,))
        c.commit()
    cfg = prime["cfg"]
    assert iv.cabe(pool, EMPRESA, cfg, _dia(1, 17), QUA_10, conversa_id=conv) == (False, "combinado")
    # na conversa DELA mesma não é choque (é ela quem está marcando)
    assert iv.cabe(pool, EMPRESA, cfg, _dia(1, 17), QUA_10, conversa_id=outra)[0]
    # outro horário no mesmo dia, livre
    assert iv.cabe(pool, EMPRESA, cfg, _dia(1, 18), QUA_10, conversa_id=conv)[0]


def test_ofertas_um_por_dia_e_confirma_no_dia_por_ultimo(pool, prime):
    of = iv.ofertas(pool, EMPRESA, prime["cfg"], QUA_10)
    # quarta 30/09 depois das 13h (3h de antecedência): o primeiro OK é 17h;
    # quinta e sexta, 9h
    assert of == [datetime(2026, 9, 30, 17, tzinfo=BRT), _dia(1, 9), _dia(2, 9)]
    assert iv.texto_ofertas(of).startswith("A) quarta 30/09 às 17h")


# ══════════════════════════════════════════════ marcar

def test_marcar_pelo_caminho_de_sempre_conta_pro_zaq_e_avisa_a_anfitria(pool, prime):
    lead, conv = _lead(pool, prime)
    r = iv.marcar(pool, EMPRESA, prime["regra"], prime["cfg"], lead, conv, _dia(1, 9, 30),
                  QUA_10, quem="Larissa")
    assert r["ok"] and "Marcado! ✅ quinta 01/10 às 9h30, com a Jacqueline." in r["texto"]
    with pool.connection() as c:
        ev = c.execute("select membro_id, marcado_por, titulo, descricao, prospeccao_id "
                       "from eventos_agenda where id=%s", (r["evento_id"],)).fetchone()
        st = c.execute("select status from prospeccao where id=%s", (lead,)).fetchone()[0]
        n = c.execute("select count(*) from ia_visitas where evento_id=%s", (r["evento_id"],)).fetchone()[0]
    assert ev[0] == prime["ids"]["ZAQ"] and ev[1] == "ia" and ev[2].startswith("Visita")
    assert "Recebe: Jacqueline" in ev[3] and ev[4] == lead
    assert st == "qualificado" and n == 1
    assert prime["avisos"][0][0] == prime["ids"]["JACQUELINE"]


def test_o_mesmo_horario_nao_e_marcado_duas_vezes(pool, prime):
    l1, c1 = _lead(pool, prime)
    l2, c2 = _lead(pool, prime, nome="Ana")
    assert iv.marcar(pool, EMPRESA, prime["regra"], prime["cfg"], l1, c1, _dia(1, 9), QUA_10)["ok"]
    r = iv.marcar(pool, EMPRESA, prime["regra"], prime["cfg"], l2, c2, _dia(1, 9), QUA_10)
    assert r == {"ok": False, "motivo": "ocupado"}


def test_remarcar_move_a_mesma_visita_ate_duas_vezes(pool, prime):
    lead, conv = _lead(pool, prime)
    a = iv.marcar(pool, EMPRESA, prime["regra"], prime["cfg"], lead, conv, _dia(1, 9), QUA_10)
    b = iv.marcar(pool, EMPRESA, prime["regra"], prime["cfg"], lead, conv, _dia(2, 10), QUA_10)
    assert b["ok"] and b["remarcou"] and b["evento_id"] == a["evento_id"]
    assert b["texto"].startswith("Remarcado! ✅ sexta 02/10 às 10h")
    with pool.connection() as c:
        assert c.execute("select count(*) from eventos_agenda").fetchone()[0] == 1
        assert c.execute("select remarcacoes from ia_visitas").fetchone()[0] == 1
    assert iv.marcar(pool, EMPRESA, prime["regra"], prime["cfg"], lead, conv, _dia(2, 11), QUA_10)["ok"]
    assert iv.marcar(pool, EMPRESA, prime["regra"], prime["cfg"], lead, conv, _dia(6, 9),
                     QUA_10) == {"ok": False, "motivo": "remarcacoes"}


def test_a_letra_do_cliente_vira_o_horario_oferecido(pool, prime):
    lead, conv = _lead(pool, prime)
    of = [_dia(1, 9), _dia(2, 9), _dia(3, 10)]
    with pool.connection() as c:
        iv.guardar_ofertas(c, EMPRESA, conv, of)
        c.commit()
        assert iv.horario_da_letra(c, EMPRESA, conv, " b ") == of[1]
        assert iv.horario_da_letra(c, EMPRESA, conv, "Opção C") == of[2]
        assert iv.horario_da_letra(c, EMPRESA, conv, "bom dia") is None
        assert iv.horario_da_letra(c, EMPRESA, conv, "a gente vai em 3") is None


# ══════════════════════════════════════════════ a resposta 1 / 2

def test_1_confirma_e_2_pede_remarcar_so_depois_da_pergunta(pool, prime):
    lead, conv = _lead(pool, prime)
    ini = datetime.now(timezone.utc) + timedelta(days=1)
    with pool.connection() as c:
        ev = c.execute("insert into eventos_agenda (conta_id, titulo, inicio, status) "
                       "values (%s,'Visita — Larissa',%s,'ativo') returning id", (EMPRESA, ini)).fetchone()[0]
        c.execute("insert into ia_visitas (evento_id, conta_id, prospeccao_id, conversa_id) "
                  "values (%s,%s,%s,%s)", (ev, EMPRESA, lead, conv))
        assert iv.responder_confirmacao(c, EMPRESA, lead, "1") == (None, None)   # sem pergunta
        c.execute("update ia_visitas set vespera_em=now()")
        o, t = iv.responder_confirmacao(c, EMPRESA, lead, "1")
        assert o == "confirmou" and t.startswith("Confirmadíssimo")
        assert iv.responder_confirmacao(c, EMPRESA, lead, "2") == (None, None)   # já respondeu
        c.execute("update ia_visitas set confirmado_em=null")
        assert iv.responder_confirmacao(c, EMPRESA, lead, "não vou conseguir")[0] == "remarcar"


# ══════════════════════════════════════════════ o relógio

@pytest.fixture()
def envios(monkeypatch):
    saiu = []

    def _mandar(c, conta, canal, destino, texto, conversa_id=None):
        saiu.append((conversa_id, texto))
        return {"ok": True, "sid": f"WA{len(saiu)}"}
    monkeypatch.setattr(agente, "_mandar", _mandar)
    return saiu


def _visita_marcada(pool, prime, ini, *, criado=None, conf_no_dia=False):
    lead, conv = _lead(pool, prime)
    with pool.connection() as c:
        ev = c.execute("insert into eventos_agenda (conta_id, titulo, inicio, fim, status, local) "
                       "values (%s,'Visita — Larissa',%s,%s,'ativo','Rua A, 10') returning id",
                       (EMPRESA, ini, ini + timedelta(hours=1))).fetchone()[0]
        c.execute("""insert into ia_visitas (evento_id, conta_id, prospeccao_id, conversa_id,
                                             conf_no_dia, criado_em)
                     values (%s,%s,%s,%s,%s,%s)""",
                  (ev, EMPRESA, lead, conv, conf_no_dia, criado or ini - timedelta(days=3)))
        c.commit()
    return ev, lead, conv


def test_vespera_as_18h_uma_vez_so(pool, prime, envios):
    ini = datetime.now(timezone.utc) + timedelta(days=1)
    ev, lead, conv = _visita_marcada(pool, prime, ini)
    loc = ini.astimezone(BRT)
    antes = datetime(loc.year, loc.month, loc.day, 17, 50, tzinfo=BRT) - timedelta(days=1)
    assert iv.rodar(pool, antes)["vesperas"] == 0
    depois = antes + timedelta(minutes=15)
    assert iv.rodar(pool, depois)["vesperas"] == 1
    assert iv.rodar(pool, depois + timedelta(minutes=2))["vesperas"] == 0
    assert envios[0][0] == conv and "Responda 1 para confirmar ou 2" in envios[0][1]
    with pool.connection() as c:
        assert c.execute("select autor from mensagens where conversa_id=%s", (conv,)).fetchone() == ("bot",)


def test_horario_que_confirma_no_dia_pergunta_as_9h_do_dia(pool, prime, envios):
    ini = datetime.now(timezone.utc) + timedelta(days=2)
    loc = ini.astimezone(BRT)
    ini = datetime(loc.year, loc.month, loc.day, 15, tzinfo=BRT)
    _visita_marcada(pool, prime, ini, conf_no_dia=True)
    vespera = datetime(loc.year, loc.month, loc.day, 18, 5, tzinfo=BRT) - timedelta(days=1)
    assert iv.rodar(pool, vespera)["vesperas"] == 0
    assert iv.rodar(pool, datetime(loc.year, loc.month, loc.day, 9, 5, tzinfo=BRT))["vesperas"] == 1
    assert envios[0][1].startswith("Oi, Larissa! Hoje às 15h")


def test_duas_horas_antes_e_ninguem_confirmou(pool, prime, envios):
    ini = datetime.now(timezone.utc) + timedelta(days=2)
    ev, lead, conv = _visita_marcada(pool, prime, ini)
    with pool.connection() as c:
        c.execute("update ia_visitas set vespera_em=now()")
        c.commit()
    r = iv.rodar(pool, ini - timedelta(minutes=110))
    assert r["duas_horas"] == 1 and "Consegue vir?" in envios[-1][1]
    r = iv.rodar(pool, ini - timedelta(minutes=80))
    assert r["sem_resposta"] == 1
    assert prime["avisos"][-1][0] == prime["ids"]["JACQUELINE"]
    assert iv.rodar(pool, ini - timedelta(minutes=70))["sem_resposta"] == 0


def test_a_falta_vira_convite_pra_remarcar(pool, prime, envios):
    ini = datetime.now(timezone.utc) - timedelta(hours=5)
    ev, lead, conv = _visita_marcada(pool, prime, ini)
    with pool.connection() as c:
        c.execute("update eventos_agenda set desfecho='nao_realizado' where id=%s", (ev,))
        c.commit()
    assert iv.rodar(pool)["faltas"] == 1
    assert iv.rodar(pool)["faltas"] == 0
    assert envios[0][1] == iv.TEXTO_FALTA


def test_envio_que_falha_devolve_o_passo(pool, prime, monkeypatch):
    monkeypatch.setattr(agente, "_mandar", lambda *a, **k: {"ok": False})
    ini = datetime.now(timezone.utc) + timedelta(days=2)
    _visita_marcada(pool, prime, ini)
    assert iv.rodar(pool, ini - timedelta(minutes=110))["duas_horas"] == 0
    with pool.connection() as c:
        assert c.execute("select duas_horas_em from ia_visitas").fetchone()[0] is None


# ══════════════════════════════════════════════ o chip da conversa

def test_confirmacao_do_painel_sai_pelo_chip_da_conversa_do_lead(pool, prime, monkeypatch):
    from finance import whatsapp_out as wo
    from web import painel_prospeccao as pp
    saiu = []
    monkeypatch.setattr(wo, "enviar", lambda c, conta, num, msg, chip_id=None:
                        saiu.append(chip_id) or {"ok": True, "sid": "X"})
    monkeypatch.setattr(pp, "_registrar_msg", lambda *a, **k: None)
    monkeypatch.setattr(ck, "entrega_sempre", lambda *a, **k: True)
    lead, conv = _lead(pool, prime)
    r = ck.agendar_visita(pool, EMPRESA, prime["ids"]["ZAQ"], lead, data="2026-10-01",
                          hora="09:30", avisar_cliente=True)
    assert r["ok"] and saiu == [CHIP2]
    ck.remarcar_visita(pool, EMPRESA, None, r["evento_id"], data="2026-10-02", hora="10:00",
                       gestao=True)
    assert saiu == [CHIP2, CHIP2]


# ══════════════════════════════════════════════ a tela

def test_salvar_confere_a_anfitria_contra_a_conta(pool, prime):
    with pool.connection() as c:
        c.execute("insert into contas (id, nome) values (99, 'Outra')")
        de_fora = c.execute("insert into membros (conta_id, nome) values (99,'X') returning id").fetchone()[0]
        assert iv.salvar(c, EMPRESA, CHIP2, {"visita_marca": True, "visita_anfitria_id": de_fora})
        assert iv.salvar(c, EMPRESA, CHIP2, {"visita_marca": True,
                                             "visita_grade": {str(d): {} for d in range(7)}})
        assert iv.salvar(c, EMPRESA, CHIP2, {"visita_marca": False, "visita_dur_min": "999"}) is None
        assert c.execute("select visita_dur_min from chip_regra").fetchone()[0] == 240


def test_config_so_existe_com_a_chave_ligada(pool, prime):
    with pool.connection() as c:
        assert iv.config(c, prime["regra"])["anfitria_id"] == prime["ids"]["JACQUELINE"]
        c.execute("update chip_regra set visita_marca=false")
        assert iv.config(c, prime["regra"]) is None
        assert iv.config_tela(c, prime["regra"]["id"])["marca"] is False


# ══════════════════════════════════════════════ o agente de ponta a ponta (IA de mentira)

_SQL_AGENTE = """
create table agente_config (conta_id bigint primary key, ativo boolean default false,
  limiar_confianca int default 80, horario text default 'comercial', tom text default 'informal',
  max_trocas int default 4, escalar_para text, pode_responder boolean default true,
  pode_qualificar boolean default false, pode_agendar boolean default false,
  pode_orcamento boolean default true, orcamento_proativo boolean default false,
  agendar_modo text default 'off');
create table agente_conhecimento (id bigserial primary key, conta_id bigint, tipo text,
  pergunta text, resposta text, ordem int default 0);
create table servicos_catalogo (id bigserial primary key, conta_id bigint, slug text,
  nome text, descricao text default '', setup_centavos bigint default 0,
  mensal_centavos bigint default 0, custo_centavos bigint default 0, ativo boolean default true,
  ordem int default 0, categoria text, foto_url text, icone text, duracao_min int,
  agente_diz_preco boolean not null default false);
"""


@pytest.fixture()
def agente_ia(pool, prime, monkeypatch):
    import json as _j
    from types import SimpleNamespace
    from core import brain as _brain
    from finance import evento_lead as _evl
    with pool.connection() as c:
        c.execute(_SQL_AGENTE)
        c.execute("insert into agente_config (conta_id) values (%s)", (EMPRESA,))
        c.commit()
    estado = {"json": {"acao": "responder", "resposta": "Oi! Quer conhecer o espaço?"},
              "prompts": [], "enviados": [], "avisos": []}

    class _Brain:
        def chamar(self, system, mensagens):
            estado["prompts"].append(mensagens[0]["content"])
            return SimpleNamespace(content=[SimpleNamespace(type="text",
                                                            text=_j.dumps(estado["json"]))])
    monkeypatch.setattr(_brain, "Brain", _Brain)
    monkeypatch.setattr(agente, "_RAJADA_S", 0)
    monkeypatch.setattr(agente, "_nota_gemeo", lambda *a, **k: "")
    monkeypatch.setattr(_evl, "gravar", lambda *a, **k: None)
    monkeypatch.setattr(agente, "_mandar", lambda c, conta, canal, dest, texto, conv=None:
                        estado["enviados"].append(texto) or {"ok": True,
                                                            "sid": f"WA{len(estado['enviados'])}"})
    monkeypatch.setattr(cr, "avisar", lambda pool_, conta, r, motivo, **k:
                        estado["avisos"].append((motivo, k.get("resumo"))))
    return estado


def _fala(pool, conv, texto, seg=30):
    with pool.connection() as c:
        c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, texto, criado_em)
                     values (%s,'whatsapp','in','lead',%s, now() - %s * interval '1 second')""",
                  (conv, texto, seg))
        c.commit()


def _visitas(pool):
    with pool.connection() as c:
        return c.execute("select inicio, marcado_por from eventos_agenda "
                         "where titulo like 'Visita%%' order by id").fetchall()


def test_a_ia_oferece_horarios_com_letra_e_guarda_a_oferta(pool, prime, agente_ia):
    lead, conv = _lead(pool, prime)
    _fala(pool, conv, "oi, queria conhecer o espaço")
    agente.atender(pool, EMPRESA, conv)
    pedir = agente_ia["prompts"][0]
    assert "você MARCA" in pedir and "A) " in pedir and "B) " in pedir
    assert "acao\":\"responder|visita" in pedir
    with pool.connection() as c:
        assert c.execute("select array_length(horarios,1) from ia_ofertas where conversa_id=%s",
                         (conv,)).fetchone()[0] == 3


def test_a_letra_marca_sem_passar_pela_ia(pool, prime, agente_ia):
    lead, conv = _lead(pool, prime)
    alvo = datetime.now(timezone.utc) + timedelta(days=5)
    loc = alvo.astimezone(BRT)
    # o dia útil de 5 dias pra frente, às 17h (segunda a sexta têm 17h na grade)
    while loc.weekday() > 4:
        loc += timedelta(days=1)
    ini = datetime(loc.year, loc.month, loc.day, 17, tzinfo=BRT)
    with pool.connection() as c:
        iv.guardar_ofertas(c, EMPRESA, conv, [ini, ini + timedelta(days=1)])
        c.commit()
    _fala(pool, conv, "A")
    agente.atender(pool, EMPRESA, conv)
    assert agente_ia["prompts"] == []
    assert agente_ia["enviados"][0].startswith("Marcado! ✅")
    assert _visitas(pool) == [(ini, "ia")]


def test_a_ia_marca_o_horario_que_o_cliente_propos(pool, prime, agente_ia):
    lead, conv = _lead(pool, prime)
    loc = (datetime.now(timezone.utc) + timedelta(days=4)).astimezone(BRT)
    while loc.weekday() not in (1, 2, 3, 4):
        loc += timedelta(days=1)
    agente_ia["json"] = {"acao": "visita", "resposta": "Dá pra acrescentar área kids 😉",
                         "visita": {"data": loc.strftime("%Y-%m-%d"), "hora": "09:30"}}
    _fala(pool, conv, f"pode ser dia {loc:%d/%m} às 9h30?")
    agente.atender(pool, EMPRESA, conv)
    txt = agente_ia["enviados"][0]
    assert txt.startswith("Marcado! ✅") and "9h30" in txt and txt.endswith("área kids 😉")
    assert _visitas(pool)[0][0] == datetime(loc.year, loc.month, loc.day, 9, 30, tzinfo=BRT)


def test_horario_ocupado_volta_com_outros_e_nao_marca(pool, prime, agente_ia):
    lead, conv = _lead(pool, prime)
    loc = (datetime.now(timezone.utc) + timedelta(days=4)).astimezone(BRT)
    while loc.weekday() not in (1, 2, 3, 4):
        loc += timedelta(days=1)
    ini = datetime(loc.year, loc.month, loc.day, 10, tzinfo=BRT)
    _evento(pool, ini, horas=1)
    agente_ia["json"] = {"acao": "visita", "resposta": "",
                         "visita": {"data": loc.strftime("%Y-%m-%d"), "hora": "10:00"}}
    _fala(pool, conv, "dia tal às 10h")
    agente.atender(pool, EMPRESA, conv)
    assert agente_ia["enviados"][0].startswith("Esse horário não está livre 😕 Tenho estes:\nA) ")
    assert _visitas(pool) == []


def test_domingo_chama_a_anfitria(pool, prime, agente_ia):
    lead, conv = _lead(pool, prime)
    loc = (datetime.now(timezone.utc) + timedelta(days=3)).astimezone(BRT)
    while loc.weekday() != 6:
        loc += timedelta(days=1)
    agente_ia["json"] = {"acao": "visita", "resposta": "",
                         "visita": {"data": loc.strftime("%Y-%m-%d"), "hora": "10:00"}}
    _fala(pool, conv, "domingo 10h?")
    agente.atender(pool, EMPRESA, conv)
    assert [m for m, _ in agente_ia["avisos"]] == ["visita"]
    assert agente_ia["enviados"][0].startswith("Domingo eu confirmo com a equipe")
    assert _visitas(pool) == []


def test_o_1_da_vespera_confirma_sem_ia(pool, prime, agente_ia):
    ini = datetime.now(timezone.utc) + timedelta(days=1)
    ev, lead, conv = _visita_marcada(pool, prime, ini)
    with pool.connection() as c:
        c.execute("update ia_visitas set vespera_em=now()")
        c.commit()
    _fala(pool, conv, "1")
    agente.atender(pool, EMPRESA, conv)
    assert agente_ia["prompts"] == [] and agente_ia["enviados"][0].startswith("Confirmadíssimo")
    with pool.connection() as c:
        assert c.execute("select confirmado_em is not null from ia_visitas").fetchone()[0]


def test_o_2_da_vespera_faz_a_ia_oferecer_outros_horarios(pool, prime, agente_ia):
    ini = datetime.now(timezone.utc) + timedelta(days=1)
    ev, lead, conv = _visita_marcada(pool, prime, ini)
    with pool.connection() as c:
        c.execute("update ia_visitas set vespera_em=now()")
        c.commit()
    _fala(pool, conv, "2")
    agente.atender(pool, EMPRESA, conv)
    assert "pediu pra REMARCAR" in agente_ia["prompts"][0]


def test_sem_a_chave_a_ia_nao_marca(pool, prime, agente_ia):
    with pool.connection() as c:
        c.execute("update chip_regra set visita_marca=false")
        c.commit()
    lead, conv = _lead(pool, prime)
    agente_ia["json"] = {"acao": "visita", "resposta": "Vou confirmar com a equipe",
                         "visita": {"data": "2026-10-01", "hora": "09:00"}}
    _fala(pool, conv, "quinta 9h")
    agente.atender(pool, EMPRESA, conv)
    assert agente_ia["enviados"] == ["Vou confirmar com a equipe"] and _visitas(pool) == []
    assert "você MARCA" not in agente_ia["prompts"][0]
