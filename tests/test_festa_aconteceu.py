"""A festa aconteceu? (finance/festa_aconteceu.py, revisão de 27/09/2026).

O card só vai pro Pós-festa — e o agradecimento só sai — depois de alguém da equipe
responder "aconteceu". A pergunta vai às 9h do dia seguinte à festa e se repete às
18h; festa cancelada na agenda não é perguntada; o card da IA pergunta pro dono.

Banco dedicado e descartável; aplica a 412 (as colunas da Prime) e a 433.
"""
import os
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from finance import agenda as ag
from finance import festa_aconteceu as fa
from finance import funil_regua as fr
from finance import visita_rotinas as vr

BRT = ag.BRT
BASE = Path(__file__).resolve().parents[1] / "db" / "migracoes"
PRIME = 34
FESTA = date(2026, 10, 3)                                   # sábado
DOM_08 = datetime(2026, 10, 4, 8, 30, tzinfo=BRT)
DOM_09 = datetime(2026, 10, 4, 9, 5, tzinfo=BRT)
DOM_18 = datetime(2026, 10, 4, 18, 3, tzinfo=BRT)

_SQL = """
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text default 'vendedor', ativo boolean default true, whatsapp text);
create table prospeccao (id bigserial primary key, conta_id bigint, empresa text, contato text,
  status text default 'novo', estagio text default 'lead', orcamento_id bigint,
  vendedor_id bigint, evento_tipo text, evento_em date, evento_convidados int,
  atualizado_em timestamptz default now(), criado_em timestamptz default now());
create table prospeccao_atividades (id bigserial primary key, prospeccao_id bigint,
  membro_id bigint, tipo text, resultado text, descricao text not null default '',
  criado_em timestamptz not null default now());
create table funil_etapas (id bigserial primary key, semeado_de text, conta_id bigint,
  chave text, rotulo text, ordem int default 0, fixa boolean default false,
  fase text default 'venda', prazo_min integer, gatilho text, gatilho_ativo boolean default false,
  teto_dias integer, renovacoes_max integer not null default 0,
  exige_justificativa boolean not null default true, renova_sozinho_h integer,
  saidas_permitidas text, toques_dias text, exige_motivo boolean not null default false,
  reativa_para text, sai_do_quadro boolean not null default false,
  agenda_ao_entrar boolean not null default false, criado_em timestamptz default now(),
  unique (conta_id, chave));
create table funil_movimentos (id bigserial primary key, conta_id bigint,
  prospeccao_id bigint, de text, para text, motivo text, membro_id bigint,
  criado_em timestamptz default now());
create table funil_regua (conta_id bigint primary key, gatilhos_modo text default 'ligado',
  cobranca_modo text default 'off', janela_dias text default '1,2,3,4,5,6',
  janela_abre time default '08:00', janela_fecha time default '19:00',
  sem_resposta_min int, bola_nossa_min int, bola_cliente_min int, escala_min int,
  teto_avisos_dia int);
create table eventos_agenda (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  titulo text, inicio timestamptz, fim timestamptz, status text default 'ativo', desfecho text,
  criado_em timestamptz default now(), tipo_evento text);
"""

_ETAPAS = [("novo", "Novo", 0, "venda", None, False),
           ("proposta", "Proposta", 40, "venda", "negociacao_valores", True),
           ("evento_realizado", "Data segurada", 70, "venda", "orcamento_aprovado", True),
           ("ganho", "Fechado", 900, "fechamento", "contrato_assinado", True),
           ("pos_festa", "Pós-festa", 905, "pos", "festa_passou", True),
           ("perdido", "Perdido", 910, "fechamento", None, False)]


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_festa_aconteceu_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity "
                  "where datname=%s and pid <> pg_backend_pid()", (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=2, max_size=5, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        for e in _ETAPAS:
            c.execute("""insert into funil_etapas (conta_id, chave, rotulo, ordem, fase, gatilho,
                                                   gatilho_ativo) values (%s,%s,%s,%s,%s,%s,%s)""",
                      (PRIME,) + e)
        c.execute("insert into funil_regua (conta_id) values (%s)", (PRIME,))
        c.execute((BASE / "433_festa_aconteceu.sql").read_text(encoding="utf-8"))
        c.commit()
    yield p
    p.close()


@pytest.fixture()
def avisos(monkeypatch):
    out = []
    monkeypatch.setattr(vr, "avisar", lambda pool, conta, mid, t, corpo, url, origem="":
                        out.append((mid, t, url, origem)) or True)
    return out


def _membro(pool, nome="Jacqueline", papel="vendedor"):
    with pool.connection() as c:
        m = c.execute("insert into membros (conta_id, nome, papel) values (%s,%s,%s) returning id",
                      (PRIME, nome, papel)).fetchone()[0]
        c.commit()
    return m


def _card(pool, vend, status="ganho", dia=FESTA, nome="Iara Souza", festa="ativo"):
    with pool.connection() as c:
        lead = c.execute("""insert into prospeccao (conta_id, contato, status, vendedor_id, evento_em,
                                                    evento_tipo)
                            values (%s,%s,%s,%s,%s,'15 anos') returning id""",
                         (PRIME, nome, status, vend, dia)).fetchone()[0]
        if festa:
            c.execute("""insert into eventos_agenda (conta_id, prospeccao_id, titulo, inicio, status,
                                                     tipo_evento)
                         values (%s,%s,'15 anos',%s,%s,'15 anos')""",
                      (PRIME, lead, datetime(dia.year, dia.month, dia.day, 20, tzinfo=BRT), festa))
        c.commit()
    return lead


def _status(pool, lead):
    with pool.connection() as c:
        return c.execute("select status from prospeccao where id=%s", (lead,)).fetchone()[0]


def test_a_pergunta_sai_as_9h_do_dia_seguinte_e_de_novo_as_18h(pool, avisos):
    v = _membro(pool)
    lead = _card(pool, v)
    assert fa.rodar(pool, DOM_08)["perguntas"] == 0                  # antes das 9h
    assert fa.rodar(pool, DOM_09)["perguntas"] == 1
    assert fa.rodar(pool, DOM_09 + timedelta(minutes=5))["perguntas"] == 0
    assert avisos[0][0] == v and avisos[0][1] == "🎉 A festa da Iara (15 anos, 03/10) aconteceu?"
    assert avisos[0][2] == f"/cockpit/lead/{lead}"
    assert fa.rodar(pool, DOM_18)["perguntas"] == 1                  # a segunda
    assert fa.rodar(pool, DOM_18 + timedelta(minutes=30))["perguntas"] == 0
    assert len(avisos) == 2


def test_sem_resposta_o_card_nao_anda_e_mostra_o_selo(pool, avisos):
    lead = _card(pool, _membro(pool))
    fa.rodar(pool, DOM_09)
    with pool.connection() as c:
        fr.aplicar_gatilhos(c, PRIME)
        c.commit()
        assert fa.selos(c, PRIME, [lead], DOM_18) == {lead: "🎉 A festa de 03/10 passou. Aconteceu?"}
    assert _status(pool, lead) == "ganho"


def test_aconteceu_leva_pro_pos_festa_e_marca_a_agenda(pool, avisos):
    v = _membro(pool)
    lead = _card(pool, v)
    r = fa.responder(pool, PRIME, lead, "aconteceu", v, DOM_09)
    assert r["ok"] and r["para"] == "pos_festa"
    assert _status(pool, lead) == "pos_festa"
    with pool.connection() as c:
        assert c.execute("select desfecho from eventos_agenda where prospeccao_id=%s",
                         (lead,)).fetchone()[0] == "realizado"
        assert c.execute("select de, para, motivo from funil_movimentos where prospeccao_id=%s",
                         (lead,)).fetchone() == ("ganho", "pos_festa", "agenda")
        assert fa.selos(c, PRIME, [lead], DOM_18) == {}
    assert fa.rodar(pool, DOM_18)["perguntas"] == 0                  # respondida, não pergunta


@pytest.mark.parametrize("resposta,trecho", [("remarcou", "REMARCADA"), ("cancelou", "CANCELADA")])
def test_remarcou_ou_cancelou_o_card_fica_com_a_nota(pool, avisos, resposta, trecho):
    v = _membro(pool)
    lead = _card(pool, v)
    assert fa.responder(pool, PRIME, lead, resposta, v, DOM_09)["ok"]
    assert _status(pool, lead) == "ganho"
    with pool.connection() as c:
        fr.aplicar_gatilhos(c, PRIME)
        c.commit()
        nota = c.execute("select descricao from prospeccao_atividades where prospeccao_id=%s",
                         (lead,)).fetchone()[0]
        assert fa.selos(c, PRIME, [lead], DOM_18) == {}
    assert trecho in nota
    assert _status(pool, lead) == "ganho"


def test_a_data_segurada_tambem_e_perguntada(pool, avisos):
    lead = _card(pool, _membro(pool), status="evento_realizado", festa="pre_reservado")
    assert fa.rodar(pool, DOM_09)["perguntas"] == 1
    assert fa.responder(pool, PRIME, lead, "aconteceu", None, DOM_09)["para"] == "pos_festa"


def test_festa_cancelada_proposta_e_festa_antiga_nao_sao_perguntadas(pool, avisos):
    v = _membro(pool)
    _card(pool, v, festa="cancelado")
    _card(pool, v, status="proposta", nome="Negociando")
    _card(pool, v, dia=date(2026, 9, 20), nome="Antiga")               # antes do "daqui pra frente"
    _card(pool, v, dia=date(2026, 10, 5), nome="Amanhã")               # ainda não passou
    assert fa.rodar(pool, DOM_09)["perguntas"] == 0


def test_o_card_da_ia_pergunta_pro_dono(pool, avisos, monkeypatch):
    from finance import chip_regra as cr
    dono = _membro(pool, "Dono", papel="dono")
    ia = _membro(pool, "ZAQ SDR")
    monkeypatch.setattr(cr, "membros_ia", lambda c, conta: {ia})
    _card(pool, ia)
    fa.rodar(pool, DOM_09)
    assert [a[0] for a in avisos] == [dono]


def test_nao_responde_antes_da_festa_nem_resposta_invalida(pool):
    v = _membro(pool)
    lead = _card(pool, v, dia=date(2026, 10, 10))
    assert not fa.responder(pool, PRIME, lead, "aconteceu", v, DOM_09)["ok"]
    assert not fa.responder(pool, PRIME, lead, "talvez", v, DOM_09)["ok"]


def test_a_migracao_433_e_idempotente(pool):
    with pool.connection() as c:
        c.execute((BASE / "433_festa_aconteceu.sql").read_text(encoding="utf-8"))
        c.commit()


def test_o_selo_as_rotas_e_o_relogio_estao_ligados():
    src_q = (Path(__file__).resolve().parents[1] / "web" / "painel_prospeccao.py").read_text(encoding="utf-8")
    src_c = (Path(__file__).resolve().parents[1] / "web" / "painel_cockpit.py").read_text(encoding="utf-8")
    src_a = (Path(__file__).resolve().parents[1] / "web" / "app.py").read_text(encoding="utf-8")
    assert '"/painel/prospeccao/{lead_id}/festa-aconteceu"' in src_q and "c.selo_aconteceu" in src_q
    assert '"/cockpit/lead/{lead_id}/festa-aconteceu"' in src_c and "_bloco_aconteceu(" in src_c
    assert "festa_aconteceu as _facs" in src_a
