"""As rotinas da visita (migração 414, finance/visita_rotinas.py) — o funil novo de
eventos, parte 2a (docs/mockups/funil_novo_rotinas.html).

O que cada bloco segura:

* A CONFIRMAÇÃO DA VISITA DA EQUIPE: ao marcar (só visita criada depois de ligar),
  véspera às 18h, 2h antes (sem a pergunta, se já confirmou) e o "ninguém confirmou"
  pra quem recebe — uma vez cada, pelo chip da conversa.
* A RESPOSTA: "1" confirma (e só o "1" puro ganha resposta automática); "2" avisa o
  dono do card e a IA não entra; o "sim" depois de a equipe falar de outra coisa não
  é resposta.
* O QUE NÃO SE CONFIRMA: a visita da IA (é do `ia_visita`), a conversa em que a IA
  está, a hora chutada pelo sistema, a conta desligada, fora do horário.
* O "VEIO?": 1h depois, pra quem recebe (na visita da IA, a anfitriã), e de novo às 18h.
* DEPOIS: veio e ninguém escreveu → o vendedor é lembrado; alguém escreveu → nada;
  visita da IA → a IA agradece. Faltou → o dono do card, se não foi ele quem recebeu.
* O SELO do card, a marcação manual, a config e a migração (Prime ligada, IA insiste
  só no chip 36).
"""
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from finance import agenda as ag
from finance import agente
from finance import cockpit as ck
from finance import follow_up as fu
from finance import visita_rotinas as vr

BASE = Path(__file__).resolve().parents[1] / "db" / "migracoes"
BRT = ag.BRT
PRIME, OUTRA, CHIP2 = 34, 35, 36
# quarta-feira 30/09/2026, 10h (Brasília)
QUA_10 = datetime(2026, 9, 30, 10, 0, tzinfo=BRT)

_SQL = """
create table nichos (id bigserial primary key, slug text, nome text);
create table contas (id bigint primary key, nome text, chip_de bigint, nicho_id bigint,
  nome_fantasia text, razao_social text, endereco text, bairro text, cidade text, uf text);
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text, ativo boolean default true, whatsapp text, whatsapp_id text);
create table prospeccao (id bigserial primary key, conta_id bigint, vendedor_id bigint,
  empresa text, contato text, whatsapp text, telefone text, status text default 'novo',
  evento_tipo text, evento_convidados int, estagio text default 'lead',
  atualizado_em timestamptz default now(), criado_em timestamptz default now());
create table eventos_agenda (id bigserial primary key, conta_id bigint, membro_id bigint,
  titulo text, inicio timestamptz, fim timestamptz, local text, descricao text,
  status text default 'ativo', tipo text default 'empresa', prospeccao_id bigint,
  tipo_evento text, hora_sugerida boolean default false, desfecho text, marcado_por text,
  criado_em timestamptz default now());
create table conversas (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  canal text default 'whatsapp', contato_ref text, status text default 'aberta',
  agente_ativo boolean default false, ultima_msg_em timestamptz default now(),
  criado_em timestamptz default now(), chip_id bigint);
create table mensagens (id bigserial primary key, conversa_id bigint, canal text,
  direcao text, autor text, texto text, provider_sid text, status text,
  criado_em timestamptz default now());
create unique index idx_mensagens_sid_conversa
  on mensagens (conversa_id, provider_sid) where provider_sid is not null;
create table funil_etapas (conta_id bigint, chave text, rotulo text, ordem int, fase text,
  gatilho text);
"""


@pytest.fixture()
def pool(monkeypatch):
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_visita_rotinas_test"
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
        c.execute("""insert into contas (id, nome, nicho_id, nome_fantasia, endereco, cidade, uf)
                     values (%s,'Prime',1,'Prime Eventos','Rua A, 10','Teresina','PI'),
                            (%s,'Outra',1,'Outra Festa',null,null,null)""", (PRIME, OUTRA))
        for m in ("388_regra_por_chip.sql", "390_ia_marca_visita.sql",
                  "401_ia_fora_da_esteira.sql"):
            c.execute((BASE / m).read_text(encoding="utf-8"))
        c.commit()
    yield p
    p.close()


@pytest.fixture()
def rec(pool, monkeypatch):
    """O que saiu: mensagens ao cliente (pelo chip da conversa) e avisos à equipe."""
    out = {"cliente": [], "zap": [], "push": []}

    def _mandar(c, conta, canal, destino, texto, conversa_id=None):
        out["cliente"].append((conversa_id, texto))
        return {"ok": True, "sid": f"s{len(out['cliente'])}"}
    monkeypatch.setattr(agente, "_mandar", _mandar)
    monkeypatch.setattr(fu, "_mandar_zap", lambda pool_, conta, numero, texto:
                        out["zap"].append((numero, texto)) or {"ok": True})
    monkeypatch.setattr(ck, "enviar_push", lambda pool_, conta, mid, t, corpo, url="", **k:
                        out["push"].append((mid, t, corpo)) or 1)
    return out


def _membro(c, nome, zap, conta=PRIME):
    return c.execute("insert into membros (conta_id, nome, whatsapp) values (%s,%s,%s) returning id",
                     (conta, nome, zap)).fetchone()[0]


@pytest.fixture()
def prime(pool, rec):
    """A Prime com as três rotinas ligadas desde segunda; a Jacqueline e o Pedro."""
    with pool.connection() as c:
        ids = {"JAC": _membro(c, "Jacqueline Souza", "5586900000001"),
               "PEDRO": _membro(c, "Pedro Lima", "5586900000002"),
               "ZAQ": _membro(c, "ZAQ SDR", "5586900000009")}
        c.execute((BASE / "414_visita_rotinas.sql").read_text(encoding="utf-8"))
        c.execute((BASE / "425_revisao_motores_parte1.sql").read_text(encoding="utf-8"))
        c.execute((BASE / "432_revisao_motores_parte2.sql").read_text(encoding="utf-8"))
        c.execute("update visita_rotinas_config set ligado_em=%s where conta_id=%s",
                  (QUA_10 - timedelta(days=3), PRIME))
        c.commit()
    return ids


def _visita(pool, dono, *, inicio=QUA_10, criado=None, recebe=None, agente_on=False,
            hora_sugerida=False, conta=PRIME, titulo="Visita — Hana", nome="Hana Lima",
            tipo=None, convidados=None, chip=None):
    """Um lead com conversa de WhatsApp e uma visita marcada. Devolve (lead, conv, evento)."""
    with pool.connection() as c:
        lead = c.execute("""insert into prospeccao (conta_id, vendedor_id, contato, whatsapp, status,
                                                    evento_tipo, evento_convidados)
                            values (%s,%s,%s,'5586988887777','qualificado',%s,%s) returning id""",
                         (conta, dono, nome, tipo, convidados)).fetchone()[0]
        conv = c.execute("""insert into conversas (conta_id, prospeccao_id, contato_ref,
                                                   agente_ativo, chip_id)
                            values (%s,%s,'5586988887777',%s,%s) returning id""",
                         (conta, lead, agente_on, chip)).fetchone()[0]
        ev = c.execute("""insert into eventos_agenda (conta_id, membro_id, titulo, inicio, fim,
                                                      prospeccao_id, hora_sugerida, criado_em)
                          values (%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
                       (conta, recebe or dono, titulo, inicio, inicio + timedelta(hours=1), lead,
                        hora_sugerida, criado or (inicio - timedelta(days=2)))).fetchone()[0]
        c.commit()
    return lead, conv, ev


def _cliente_diz(pool, conv, texto, quando):
    with pool.connection() as c:
        c.execute("insert into mensagens (conversa_id, canal, direcao, autor, texto, criado_em) "
                  "values (%s,'whatsapp','in','cliente',%s,%s)", (conv, texto, quando))
        c.commit()


def _estado(pool, ev):
    with pool.connection() as c:
        r = c.execute("select * from visita_rotinas where evento_id=%s", (ev,)).fetchone()
        cols = [d.name for d in c.execute("select * from visita_rotinas limit 0").description]
    return dict(zip(cols, r)) if r else {}


def _em(dt_brt):
    return dt_brt


# ══════════════════════════════════════════════ a confirmação da visita da equipe

def test_ao_marcar_vespera_e_duas_horas_saem_uma_vez_pelo_chip_da_conversa(pool, rec, prime):
    lead, conv, ev = _visita(pool, prime["JAC"], criado=datetime(2026, 9, 29, 10, 55, tzinfo=BRT))
    ter_11 = datetime(2026, 9, 29, 11, 0, tzinfo=BRT)
    vr.rodar(pool, ter_11)
    vr.rodar(pool, ter_11 + timedelta(minutes=2))
    assert len(rec["cliente"]) == 1
    conv_id, txt = rec["cliente"][0]
    assert conv_id == conv
    assert txt.startswith("Oi, Hana! Sua visita ao Prime Eventos está marcada:")
    assert "com Jacqueline" in txt and "Na véspera eu te chamo pra confirmar" in txt
    # a véspera, às 18h
    vr.rodar(pool, datetime(2026, 9, 29, 18, 3, tzinfo=BRT))
    assert "Amanhã às 10h é a sua visita ao Prime Eventos" in rec["cliente"][-1][1]
    assert "Responda 1 para confirmar ou 2" in rec["cliente"][-1][1]
    # 2h antes, sem resposta: ainda pergunta
    vr.rodar(pool, datetime(2026, 9, 30, 8, 5, tzinfo=BRT))
    assert rec["cliente"][-1][1].startswith("Daqui a pouco, às 10h, te esperamos no Prime Eventos!")
    assert "Consegue vir?" in rec["cliente"][-1][1]
    assert len(rec["cliente"]) == 3
    # e o que foi mandado ficou gravado na conversa, como do bot
    with pool.connection() as c:
        assert c.execute("select count(*) from mensagens where conversa_id=%s and autor='bot'",
                         (conv,)).fetchone()[0] == 3


def test_ninguem_confirmou_avisa_quem_recebe_uma_vez(pool, rec, prime):
    lead, conv, ev = _visita(pool, prime["PEDRO"], recebe=prime["JAC"])
    vr.rodar(pool, datetime(2026, 9, 29, 18, 3, tzinfo=BRT))
    vr.rodar(pool, datetime(2026, 9, 30, 8, 5, tzinfo=BRT))
    vr.rodar(pool, datetime(2026, 9, 30, 8, 35, tzinfo=BRT))
    vr.rodar(pool, datetime(2026, 9, 30, 8, 40, tzinfo=BRT))
    avisos = [z for z in rec["zap"] if "sem confirmação" in z[1]]
    assert len(avisos) == 1
    assert avisos[0][0] == "5586900000001"                       # a Jacqueline, quem recebe
    assert "Hana não confirmou a visita das 10h. Continua marcada." in avisos[0][1]
    assert [p for p in rec["push"] if p[0] == prime["JAC"]]


def test_o_1_confirma_e_ganha_a_resposta_e_o_2h_sai_sem_pergunta(pool, rec, prime):
    lead, conv, ev = _visita(pool, prime["JAC"])
    vr.rodar(pool, datetime(2026, 9, 29, 18, 3, tzinfo=BRT))
    _cliente_diz(pool, conv, "1", datetime(2026, 9, 29, 18, 30, tzinfo=BRT))
    vr.rodar(pool, datetime(2026, 9, 29, 18, 32, tzinfo=BRT))
    assert _estado(pool, ev)["confirmado_em"] is not None
    assert rec["cliente"][-1][1] == "Confirmadíssimo! 🎉 Te esperamos quarta 30/09 às 10h."
    vr.rodar(pool, datetime(2026, 9, 30, 8, 5, tzinfo=BRT))
    assert "Consegue vir?" not in rec["cliente"][-1][1]
    # confirmada não gera o "ninguém confirmou"
    vr.rodar(pool, datetime(2026, 9, 30, 8, 35, tzinfo=BRT))
    assert not [z for z in rec["zap"] if "sem confirmação" in z[1]]
    with pool.connection() as c:
        assert vr.selos(c, PRIME, [lead]) == {lead: ("confirmada ✓", "ok")}


def test_sim_com_pergunta_confirma_mas_a_resposta_fica_pro_vendedor(pool, rec, prime):
    lead, conv, ev = _visita(pool, prime["JAC"])
    vr.rodar(pool, datetime(2026, 9, 29, 18, 3, tzinfo=BRT))
    n = len(rec["cliente"])
    _cliente_diz(pool, conv, "sim, qual o endereço mesmo?", datetime(2026, 9, 29, 18, 30, tzinfo=BRT))
    vr.rodar(pool, datetime(2026, 9, 29, 18, 32, tzinfo=BRT))
    assert _estado(pool, ev)["confirmado_em"] is not None
    assert len(rec["cliente"]) == n


def test_o_2_avisa_o_dono_do_card_e_a_ia_nao_entra(pool, rec, prime):
    lead, conv, ev = _visita(pool, prime["PEDRO"], recebe=prime["JAC"])
    vr.rodar(pool, datetime(2026, 9, 29, 18, 3, tzinfo=BRT))
    _cliente_diz(pool, conv, "2", datetime(2026, 9, 29, 19, 0, tzinfo=BRT))
    vr.rodar(pool, datetime(2026, 9, 29, 19, 2, tzinfo=BRT))
    assert _estado(pool, ev)["pede_remarcar_em"] is not None
    assert rec["cliente"][-1][1] == "Tudo bem! Pedro já vai falar com você pra achar outro horário 😊"
    aviso = [z for z in rec["zap"] if "pediu pra remarcar" in z[1]]
    assert aviso and aviso[0][0] == "5586900000002"               # o Pedro, dono do card
    # e daqui pra frente nada mais sai pro cliente sobre esta visita
    n = len(rec["cliente"])
    vr.rodar(pool, datetime(2026, 9, 30, 8, 5, tzinfo=BRT))
    assert len(rec["cliente"]) == n
    with pool.connection() as c:
        assert vr.selos(c, PRIME, [lead])[lead] == ("pediu pra remarcar", "bad")


def test_sim_depois_de_a_equipe_falar_de_outra_coisa_nao_e_resposta(pool, rec, prime):
    lead, conv, ev = _visita(pool, prime["JAC"])
    vr.rodar(pool, datetime(2026, 9, 29, 18, 3, tzinfo=BRT))
    with pool.connection() as c:
        c.execute("insert into mensagens (conversa_id, canal, direcao, autor, texto, criado_em) "
                  "values (%s,'whatsapp','out','humano','quer que eu mande o cardápio?',%s)",
                  (conv, datetime(2026, 9, 29, 18, 20, tzinfo=BRT)))
        c.commit()
    _cliente_diz(pool, conv, "sim", datetime(2026, 9, 29, 18, 25, tzinfo=BRT))
    vr.rodar(pool, datetime(2026, 9, 29, 18, 30, tzinfo=BRT))
    assert _estado(pool, ev)["confirmado_em"] is None
    # NEM O "1" PURO (revisão de 27/09/2026): depois que a equipe fala, o número pode
    # estar respondendo a ela ("salão 1 ou 2?"). A conversa é da equipe, e quem confirma
    # é ela, pelo app
    _cliente_diz(pool, conv, "1", datetime(2026, 9, 29, 18, 40, tzinfo=BRT))
    vr.rodar(pool, datetime(2026, 9, 29, 18, 42, tzinfo=BRT))
    assert _estado(pool, ev)["confirmado_em"] is None
    assert len(rec["cliente"]) == 1                              # e nada saiu ao cliente


def test_o_vendedor_marca_confirmada_e_as_mensagens_param(pool, rec, prime):
    lead, conv, ev = _visita(pool, prime["JAC"], inicio=datetime.now(BRT) + timedelta(days=5))
    with pool.connection() as c:
        assert not vr.marcar_confirmada(c, PRIME, ev, prime["PEDRO"])     # não é dele
        assert not vr.marcar_confirmada(c, OUTRA, ev, None, gestao=True)   # outra conta
        assert vr.marcar_confirmada(c, PRIME, ev, prime["JAC"])
        c.commit()
    assert _estado(pool, ev)["confirmado_em"] is not None


def test_marcada_pelo_app_com_aviso_nao_ganha_o_ao_marcar_de_novo(pool, rec, prime):
    criado = datetime(2026, 9, 29, 10, 55, tzinfo=BRT)
    lead, conv, ev = _visita(pool, prime["JAC"], criado=criado)
    with pool.connection() as c:
        c.execute("insert into mensagens (conversa_id, canal, direcao, autor, texto, criado_em) "
                  "values (%s,'whatsapp','out','humano','Olá! 👋 Sua visita ao Prime Eventos está marcada',%s)",
                  (conv, criado + timedelta(seconds=5)))
        c.commit()
    vr.rodar(pool, datetime(2026, 9, 29, 11, 0, tzinfo=BRT))
    assert rec["cliente"] == []
    assert _estado(pool, ev)["ao_marcar_em"] is not None
    # a véspera continua saindo
    vr.rodar(pool, datetime(2026, 9, 29, 18, 3, tzinfo=BRT))
    assert "Responda 1 para confirmar" in rec["cliente"][-1][1]


def test_remarcar_recomeca_a_confirmacao_no_horario_novo(pool, rec, prime):
    lead, conv, ev = _visita(pool, prime["JAC"])
    vr.rodar(pool, datetime(2026, 9, 29, 18, 3, tzinfo=BRT))
    _cliente_diz(pool, conv, "1", datetime(2026, 9, 29, 18, 30, tzinfo=BRT))
    vr.rodar(pool, datetime(2026, 9, 29, 18, 32, tzinfo=BRT))
    assert _estado(pool, ev)["confirmado_em"] is not None
    novo = datetime(2026, 10, 2, 10, 0, tzinfo=BRT)
    with pool.connection() as c:
        c.execute("update eventos_agenda set inicio=%s where id=%s", (novo, ev))
        c.commit()
    vr.rodar(pool, datetime(2026, 9, 29, 19, 0, tzinfo=BRT))     # o ciclo seguinte vê
    st = _estado(pool, ev)
    assert st["confirmado_em"] is None and st["inicio_visto"] == novo
    vr.rodar(pool, datetime(2026, 10, 1, 18, 3, tzinfo=BRT))
    assert "Amanhã às 10h" in rec["cliente"][-1][1]


def test_remarcar_depois_das_18h_pra_amanha_nao_manda_a_vespera_na_hora(pool, rec, prime):
    """Revisão de 27/09/2026: o remarcar já avisou ("sua visita mudou de data"); a
    pergunta da véspera logo em seguida era a mesma notícia outra vez. O "2h antes"
    continua saindo."""
    lead, conv, ev = _visita(pool, prime["JAC"], inicio=datetime(2026, 10, 5, 10, 0, tzinfo=BRT),
                             criado=datetime(2026, 9, 28, 10, 0, tzinfo=BRT))
    vr.rodar(pool, datetime(2026, 9, 30, 12, 0, tzinfo=BRT))      # acompanhada
    novo = datetime(2026, 10, 1, 11, 0, tzinfo=BRT)
    with pool.connection() as c:
        c.execute("update eventos_agenda set inicio=%s where id=%s", (novo, ev))
        c.commit()
    antes = len(rec["cliente"])
    vr.rodar(pool, datetime(2026, 9, 30, 18, 22, tzinfo=BRT))     # remarcou às 18h20
    vr.rodar(pool, datetime(2026, 9, 30, 18, 40, tzinfo=BRT))
    assert len(rec["cliente"]) == antes
    vr.rodar(pool, datetime(2026, 10, 1, 9, 5, tzinfo=BRT))
    assert rec["cliente"][-1][1].startswith("Daqui a pouco, às 11h")


def test_o_ao_marcar_espera_o_aviso_de_quem_marcou_sair(pool, rec, prime):
    """Revisão de 27/09/2026: quem marca pelo app avisa o cliente, e o aviso leva
    segundos pra sair e ser gravado. O relógio espera 3 min antes de decidir."""
    criado = datetime(2026, 9, 29, 10, 59, tzinfo=BRT)
    lead, conv, ev = _visita(pool, prime["JAC"], criado=criado)
    vr.rodar(pool, datetime(2026, 9, 29, 11, 0, tzinfo=BRT))      # 1 min depois: espera
    assert rec["cliente"] == []
    with pool.connection() as c:                                   # o aviso do app chegou
        c.execute("insert into mensagens (conversa_id, canal, direcao, autor, texto, criado_em) "
                  "values (%s,'whatsapp','out','humano','Sua visita está marcada',%s)",
                  (conv, datetime(2026, 9, 29, 11, 0, 30, tzinfo=BRT)))
        c.commit()
    vr.rodar(pool, datetime(2026, 9, 29, 11, 3, tzinfo=BRT))
    assert rec["cliente"] == []                                    # não repete a notícia
    assert _estado(pool, ev)["ao_marcar_em"] is not None


# ══════════════════════════════════════════════ o que não se confirma

def test_ao_marcar_nao_sai_pra_visita_criada_antes_de_ligar(pool, rec, prime):
    _visita(pool, prime["JAC"], criado=datetime(2026, 9, 26, 10, 0, tzinfo=BRT))
    vr.rodar(pool, datetime(2026, 9, 29, 11, 0, tzinfo=BRT))
    assert rec["cliente"] == []


def test_visita_da_ia_conversa_da_ia_e_hora_chutada_nao_recebem(pool, rec, prime):
    _l1, _c1, ev_ia = _visita(pool, prime["ZAQ"])
    with pool.connection() as c:
        c.execute("insert into ia_visitas (evento_id, conta_id, prospeccao_id, conversa_id) "
                  "values (%s,%s,%s,%s)", (ev_ia, PRIME, _l1, _c1))
        c.commit()
    _visita(pool, prime["JAC"], agente_on=True)
    _visita(pool, prime["JAC"], hora_sugerida=True)
    vr.rodar(pool, datetime(2026, 9, 29, 18, 3, tzinfo=BRT))
    vr.rodar(pool, datetime(2026, 9, 30, 8, 5, tzinfo=BRT))
    assert rec["cliente"] == []


def test_conta_desligada_e_fora_do_horario_nao_recebem(pool, rec, prime):
    _visita(pool, prime["JAC"], conta=OUTRA)
    lead, conv, ev = _visita(pool, prime["JAC"], inicio=datetime(2026, 9, 30, 21, 0, tzinfo=BRT))
    # véspera às 18h da visita das 21h: só às 20h59 ainda cabe; às 20h10 não é cliente-ok
    vr.rodar(pool, datetime(2026, 9, 29, 20, 10, tzinfo=BRT))
    assert rec["cliente"] == []
    vr.rodar(pool, datetime(2026, 9, 30, 8, 0, tzinfo=BRT))
    assert len(rec["cliente"]) == 1 and "Hoje às 21h" in rec["cliente"][0][1]


def test_so_titulo_de_visita_conta_pra_quem_vende_festa(pool, rec, prime):
    _visita(pool, prime["JAC"], titulo="Ligar pro cliente")
    vr.rodar(pool, datetime(2026, 9, 29, 18, 3, tzinfo=BRT))
    assert rec["cliente"] == []


# ══════════════════════════════════════════════ o "veio?"

def test_veio_1h_depois_e_de_novo_as_18h_pra_quem_recebeu(pool, rec, prime):
    lead, conv, ev = _visita(pool, prime["PEDRO"], recebe=prime["JAC"])
    vr.rodar(pool, datetime(2026, 9, 30, 10, 50, tzinfo=BRT))
    assert not [z for z in rec["zap"] if "veio?" in z[1]]
    vr.rodar(pool, datetime(2026, 9, 30, 11, 2, tzinfo=BRT))
    vr.rodar(pool, datetime(2026, 9, 30, 11, 4, tzinfo=BRT))
    veio = [z for z in rec["zap"] if "veio?" in z[1]]
    assert len(veio) == 1 and veio[0][0] == "5586900000001"
    assert "📍 Visita das 10h: Hana veio?" in veio[0][1]
    vr.rodar(pool, datetime(2026, 9, 30, 18, 1, tzinfo=BRT))
    vr.rodar(pool, datetime(2026, 9, 30, 18, 5, tzinfo=BRT))
    assert len([z for z in rec["zap"] if "veio?" in z[1]]) == 2


def test_respondido_nao_pergunta_mais(pool, rec, prime):
    lead, conv, ev = _visita(pool, prime["JAC"])
    with pool.connection() as c:
        c.execute("update eventos_agenda set desfecho='realizado' where id=%s", (ev,))
        c.commit()
    vr.rodar(pool, datetime(2026, 9, 30, 11, 2, tzinfo=BRT))
    assert not [z for z in rec["zap"] if "veio?" in z[1]]


def test_na_visita_da_ia_o_veio_vai_pra_anfitria(pool, rec, prime):
    with pool.connection() as c:
        c.execute("""insert into chip_regra (conta_id, chip_id, ativa, membro_id, ia_ligada,
                                             visita_marca, visita_anfitria_id)
                     values (%s,%s,true,%s,true,true,%s)""",
                  (PRIME, CHIP2, prime["ZAQ"], prime["JAC"]))
        c.commit()
    lead, conv, ev = _visita(pool, prime["ZAQ"], agente_on=True, chip=CHIP2)
    with pool.connection() as c:
        c.execute("insert into chip_regra_leads (conta_id, prospeccao_id, chip_id, membro_id) "
                  "values (%s,%s,%s,%s)", (PRIME, lead, CHIP2, prime["ZAQ"]))
        c.execute("insert into ia_visitas (evento_id, conta_id, prospeccao_id, conversa_id) "
                  "values (%s,%s,%s,%s)", (ev, PRIME, lead, conv))
        c.commit()
    vr.rodar(pool, datetime(2026, 9, 30, 11, 2, tzinfo=BRT))
    veio = [z for z in rec["zap"] if "veio?" in z[1]]
    assert len(veio) == 1 and veio[0][0] == "5586900000001"       # a anfitriã


# ══════════════════════════════════════════════ depois da visita

def test_veio_e_ninguem_escreveu_lembra_o_vendedor(pool, rec, prime):
    lead, conv, ev = _visita(pool, prime["PEDRO"], recebe=prime["JAC"])
    with pool.connection() as c:
        c.execute("update eventos_agenda set desfecho='realizado' where id=%s", (ev,))
        c.commit()
    vr.rodar(pool, datetime(2026, 9, 30, 12, 50, tzinfo=BRT))
    assert not [z for z in rec["zap"] if "Fale com" in z[1]]
    vr.rodar(pool, datetime(2026, 9, 30, 13, 2, tzinfo=BRT))
    vr.rodar(pool, datetime(2026, 9, 30, 13, 5, tzinfo=BRT))
    fale = [z for z in rec["zap"] if "Fale com" in z[1]]
    assert len(fale) == 1 and fale[0][0] == "5586900000002"       # o dono do card
    assert "Mande a proposta enquanto a festa está fresca" in fale[0][1]
    with pool.connection() as c:
        assert vr.selos(c, PRIME, [lead])[lead] == ("falar com o cliente", "at")
        # o vendedor escreveu: o selo sai
        c.execute("insert into mensagens (conversa_id, canal, direcao, autor, texto, criado_em) "
                  "values (%s,'whatsapp','out','humano','oi Hana!',%s)",
                  (conv, datetime(2026, 9, 30, 13, 20, tzinfo=BRT)))
        c.commit()
    vr.rodar(pool, datetime(2026, 9, 30, 13, 25, tzinfo=BRT))
    assert _estado(pool, ev)["depois_acao"] == "respondido"
    with pool.connection() as c:
        assert lead not in vr.selos(c, PRIME, [lead])


def test_veio_e_alguem_ja_escreveu_nao_lembra(pool, rec, prime):
    lead, conv, ev = _visita(pool, prime["PEDRO"])
    with pool.connection() as c:
        c.execute("update eventos_agenda set desfecho='realizado' where id=%s", (ev,))
        c.execute("insert into mensagens (conversa_id, canal, direcao, autor, texto, criado_em) "
                  "values (%s,'whatsapp','out','humano','foi ótimo te receber',%s)",
                  (conv, datetime(2026, 9, 30, 11, 30, tzinfo=BRT)))
        c.commit()
    vr.rodar(pool, datetime(2026, 9, 30, 13, 2, tzinfo=BRT))
    assert not [z for z in rec["zap"] if "Fale com" in z[1]]
    assert _estado(pool, ev)["depois_acao"] == "ja_falou"


def test_na_visita_da_ia_a_ia_agradece_e_oferece_o_orcamento(pool, rec, prime):
    lead, conv, ev = _visita(pool, prime["ZAQ"], agente_on=True, tipo="15 anos", convidados=150)
    with pool.connection() as c:
        c.execute("insert into ia_visitas (evento_id, conta_id, prospeccao_id, conversa_id) "
                  "values (%s,%s,%s,%s)", (ev, PRIME, lead, conv))
        c.execute("update eventos_agenda set desfecho='realizado' where id=%s", (ev,))
        c.commit()
    vr.rodar(pool, datetime(2026, 9, 30, 13, 2, tzinfo=BRT))
    assert rec["cliente"] == [(conv, "Oi, Hana! Foi um prazer te receber hoje 😊 Gostou do espaço? "
                                     "Já posso montar o orçamento da sua festa (15 anos, 150 convidados)?")]
    with pool.connection() as c:
        assert vr.selos(c, PRIME, [lead])[lead] == ("agradecida ✓", "ia")


def test_faltou_avisa_o_dono_do_card_so_se_outro_recebeu(pool, rec, prime):
    _l1, _c1, ev1 = _visita(pool, prime["PEDRO"], recebe=prime["JAC"])
    _l2, _c2, ev2 = _visita(pool, prime["JAC"], nome="Rui Costa")
    with pool.connection() as c:
        c.execute("update eventos_agenda set desfecho='nao_realizado' where id in (%s,%s)", (ev1, ev2))
        c.commit()
    vr.rodar(pool, datetime(2026, 9, 30, 11, 30, tzinfo=BRT))
    falta = [z for z in rec["zap"] if "faltou" in z[1]]
    assert len(falta) == 1 and falta[0][0] == "5586900000002"
    assert "❌ Hana faltou à visita" in falta[0][1]


# ══════════════════════════════════════════════ a config e a migração

def test_a_migracao_liga_so_a_prime_e_a_ia_insiste_so_no_chip_36(pool, rec):
    with pool.connection() as c:
        zaq = _membro(c, "ZAQ SDR", "1")
        c.execute("""insert into chip_regra (conta_id, chip_id, ativa, membro_id, ia_ligada)
                     values (%s,%s,true,%s,true), (%s,37,true,%s,true)""",
                  (PRIME, CHIP2, zaq, PRIME, zaq))
        sql = (BASE / "414_visita_rotinas.sql").read_text(encoding="utf-8")
        c.execute(sql)
        c.execute(sql)                                           # idempotente
        c.commit()
        assert vr.config(c, PRIME)["confirmar"] and vr.config(c, PRIME)["depois_visita"]
        assert not vr.config(c, OUTRA)["confirmar"]
        assert dict(c.execute("select chip_id, ia_insiste from chip_regra").fetchall()) == \
            {CHIP2: True, 37: False}


def test_salvar_config_so_move_o_ligado_em_quando_liga(pool, rec):
    with pool.connection() as c:
        c.execute((BASE / "414_visita_rotinas.sql").read_text(encoding="utf-8"))
        vr.salvar_config(c, OUTRA, {"confirmar": True})
        c.commit()
        l1 = vr.config(c, OUTRA)["ligado_em"]
        assert l1 is not None
        vr.salvar_config(c, OUTRA, {"confirmar": True, "perguntar_veio": False})
        c.commit()
        assert vr.config(c, OUTRA)["ligado_em"] == l1
        vr.salvar_config(c, OUTRA, {})
        c.commit()
        assert not any(vr.config(c, OUTRA)[k] for k in vr.CHAVES)


def test_sem_as_tabelas_nada_acontece(rec):
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_visita_rotinas_vazio_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    try:
        assert not any(vr.rodar(p).values())
        with p.connection() as c:
            assert vr.config(c, PRIME)["confirmar"] is False
            assert vr.selos(c, PRIME, [1]) == {}
    finally:
        p.close()


# ══════════════════════════════════════════════ as telas

def test_o_bloco_da_regua_so_pra_quem_vende_festa_e_a_rota_salva_as_tres():
    import inspect
    from web import painel_prospeccao as pp
    tpl = pp._REGUA_TPL
    bloco = tpl[tpl.index('{% if rotinas_festa is defined and rotinas_festa is not none %}'):]
    # desde 27/09/2026 (a régua reorganizada) as rotinas vêm depois das automações,
    # logo antes dos motivos de perda
    bloco = bloco[:bloco.index("{% endif %}\n\n  <!-- ---------------- motivos de perda")]
    assert 'action="/painel/prospeccao/regua/rotinas-festa"' in bloco
    for k in vr.CHAVES:
        assert f"('{k}'," in bloco
    assert "{{ voc.cliente }}" in bloco
    fonte = inspect.getsource(pp.regua_pagina)
    assert 'if perfil_chave == "eventos":' in fonte and "rotinas_festa=rotinas_festa" in fonte
    rota = inspect.getsource(pp.regua_rotinas_festa)
    assert '!= "eventos"' in rota and "_vrt.salvar_config" in rota
    assert "async def" not in rota, "banco síncrono no event loop"


def test_o_selo_no_card_e_o_botao_no_app():
    import inspect
    from web import painel_cockpit as pc
    from web import painel_prospeccao as pp
    assert '{% if c.selo_visita %}<div class="kbvis {{ c.selo_visita[1] }}"' in pp._KANBAN_TPL
    assert 'cc["selo_visita"] = selo_visita.get(cc["id"])' in inspect.getsource(pp)
    fonte = inspect.getsource(pc)
    assert "/agenda/{v['id']}/confirmada" in fonte and "já confirmou</button>" in fonte
    assert "_vrt.marcar_confirmada(c, conta_id, ev_id, membro_id, gestao=bool(g))" in \
        inspect.getsource(pc.cockpit_visita_confirmada)


def test_os_templates_compilam():
    from web import painel_prospeccao as pp
    pp._env.parse(pp._REGUA_TPL)
    pp._env.parse(pp._KANBAN_TPL)


# ══════════════════════════════════════════════ a revisão de 27/09/2026 (parte 1)

@pytest.mark.parametrize("status", ["perdido", "ganho", "lista_espera"])
def test_card_fora_do_jogo_nao_recebe_a_vespera(pool, rec, prime, status):
    lead, conv, ev = _visita(pool, prime["JAC"])
    with pool.connection() as c:
        c.execute("update prospeccao set status=%s where id=%s", (status, lead))
        c.commit()
    vr.rodar(pool, datetime(2026, 9, 29, 18, 3, tzinfo=BRT))
    vr.rodar(pool, datetime(2026, 9, 30, 8, 5, tzinfo=BRT))
    vr.rodar(pool, datetime(2026, 9, 30, 8, 35, tzinfo=BRT))
    assert rec["cliente"] == []
    assert not [z for z in rec["zap"] if "sem confirmação" in z[1].lower()]


def test_quem_pediu_pra_parar_nao_recebe_mais_nada(pool, rec, prime):
    lead, conv, ev = _visita(pool, prime["JAC"])
    _cliente_diz(pool, conv, "não quero mais, obrigada", datetime(2026, 9, 29, 12, 0, tzinfo=BRT))
    vr.rodar(pool, datetime(2026, 9, 29, 18, 3, tzinfo=BRT))
    vr.rodar(pool, datetime(2026, 9, 30, 8, 5, tzinfo=BRT))
    assert rec["cliente"] == []


def test_o_membro_da_ia_nao_recebe_aviso_quem_responde_e_gente(pool, rec, prime):
    """O 'ZAQ SDR' é dono de uma regra com a IA ligada: o WhatsApp dele é o do dono da
    conta. A visita na agenda dele, com o card da Jacqueline: o 'veio?' vai pra ela."""
    with pool.connection() as c:
        c.execute("""insert into chip_regra (conta_id, chip_id, ativa, membro_id, ia_ligada)
                     values (%s,%s,true,%s,true)""", (PRIME, CHIP2, prime["ZAQ"]))
        c.commit()
    _l1, _c1, ev1 = _visita(pool, prime["JAC"], recebe=prime["ZAQ"])
    vr.rodar(pool, datetime(2026, 9, 30, 11, 2, tzinfo=BRT))
    veio = [z for z in rec["zap"] if "veio?" in z[1]]
    assert [z[0] for z in veio] == ["5586900000001"]                 # a Jacqueline
    # e a visita que só tem a IA (card e agenda dela): nenhum aviso sai
    _l2, _c2, ev2 = _visita(pool, prime["ZAQ"], nome="Rui Costa")
    vr.rodar(pool, datetime(2026, 9, 30, 11, 4, tzinfo=BRT))
    assert not [z for z in rec["zap"] if z[0] == "5586900000009"]


def test_o_teto_do_chip_segura_e_solta_depois(pool, rec, prime, monkeypatch):
    from finance import teto_chip as tc
    lead, conv, ev = _visita(pool, prime["JAC"], chip=CHIP2)
    vespera = datetime(2026, 9, 29, 18, 3, tzinfo=BRT)
    with pool.connection() as c:
        for i in range(tc.POR_HORA):
            c.execute("""insert into envios_automaticos (conta_id, chip_id, origem, criado_em)
                         values (%s,%s,'teste',%s)""",
                      (PRIME, CHIP2, vespera - timedelta(minutes=30)))
        c.commit()
    vr.rodar(pool, vespera)
    assert rec["cliente"] == []
    assert _estado(pool, ev)["vespera_em"] is None                  # não gastou o passo
    assert _estado(pool, ev)["envio_falhas"] == 0                   # nem contou falha
    # uma hora depois, o teto da hora abriu: sai, e conta
    vr.rodar(pool, vespera + timedelta(minutes=35))
    assert len(rec["cliente"]) == 1 and "Amanhã às 10h" in rec["cliente"][0][1]
    with pool.connection() as c:
        assert c.execute("select count(*) from envios_automaticos where origem='visita'"
                         ).fetchone()[0] == 1
    # outro chip não é afetado pelo teto deste
    with pool.connection() as c:
        assert tc.pode(c, PRIME, None, vespera) is True


def test_o_prazo_estourado_conta_como_enviado(pool, rec, prime, monkeypatch):
    lead, conv, ev = _visita(pool, prime["JAC"])
    tentativas = []

    def _lento(c, conta, canal, destino, texto, conversa_id=None):
        tentativas.append(texto)
        return {"ok": False, "erro": "Read timed out. (read timeout=15)"}
    monkeypatch.setattr(agente, "_mandar", _lento)
    vr.rodar(pool, datetime(2026, 9, 29, 18, 3, tzinfo=BRT))
    vr.rodar(pool, datetime(2026, 9, 29, 18, 30, tzinfo=BRT))
    vr.rodar(pool, datetime(2026, 9, 29, 19, 0, tzinfo=BRT))
    assert len(tentativas) == 1                                     # não manda de novo
    assert _estado(pool, ev)["vespera_em"] is not None
