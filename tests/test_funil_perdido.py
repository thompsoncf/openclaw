"""O Perdido automático (finance/funil_perdido): quem fecha, e quem NUNCA fecha.

O que estes testes protegem, em uma frase cada:
  * o lead com o CLIENTE esperando resposta nunca é fechado — nem no 30º dia, nem
    com as renovações esgotadas, nem com cinco toques em cima;
  * a bola é decidida por ID, não por data (duas mensagens quase simultâneas
    chegam fora de ordem, e empatar pro lado errado fecha quem levantou a mão);
  * passar do prazo NÃO basta: sem os toques mínimos o lead fica — foi a empresa
    que não fez follow-up, e o carimbo seria dele;
  * o toque do celular conta igual ao do app (pro cliente é a mesma mensagem);
  * o corte por data (`perdido_desde`) segura a fila velha;
  * 'off' não olha nada, 'observando' conta sem fechar, 'ligado' fecha;
  * fechar grava motivo na ficha E linha no histórico, e quem fecha é a regra
    ('sem_resposta'), nunca 'manual';
  * se uma pessoa mexeu no lead no meio do caminho, a regra se cala.

Banco descartável, `agora` sempre injetado — mesmo padrão do teste da trava.
"""
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from finance import funil_perdido as fp

MIG = Path(__file__).resolve().parent.parent / "db" / "migracoes"
CONTA = 12
AGORA = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)

_SQL = """
create table prospeccao (id bigserial primary key, conta_id bigint, empresa text, contato text,
  status text default 'novo', estagio text default 'lead', vendedor_id bigint,
  proximo_contato_em timestamptz, atualizado_em timestamptz default now(),
  criado_em timestamptz default now());
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text default 'vendedor', ativo boolean default true);
create table conversas (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  canal text default 'whatsapp');
create table mensagens (id bigserial primary key, conversa_id bigint, direcao text,
  autor text default 'humano', membro_id bigint, texto text default '',
  criado_em timestamptz default now());
create table funil_movimentos (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  de text, para text, motivo text, membro_id bigint, criado_em timestamptz default now());
create table funil_avisos (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  estado text, nivel text, etapa text default '', ref_em timestamptz, simulado boolean default false,
  membro_id bigint, criado_em timestamptz default now());
create table funil_etapas (id bigserial primary key, semeado_de text, conta_id bigint, chave text,
  rotulo text, ordem int default 0, fixa boolean default false, fase text default 'venda',
  prazo_min integer, gatilho text, gatilho_ativo boolean default false);
create table funil_regua (conta_id bigint primary key,
  gatilhos_modo text not null default 'off', cobranca_modo text not null default 'off',
  janela_dias text, janela_abre time, janela_fecha time,
  sem_resposta_min int, bola_nossa_min int, bola_cliente_min int,
  escala_min int, teto_avisos_dia int,
  follow_up_modo text not null default 'off', fu_proposta_dias int, fu_toques_dias text,
  fu_zap boolean not null default false, fu_festa_dias int, fu_teto_dia int,
  atualizado_em timestamptz not null default now());
"""


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_funil_perdido_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        # as migrações DE VERDADE, na ordem em que o Render aplica
        for nome in ("209_raio_x_dono.sql", "213_perda_motivo_por_perfil.sql",
                     "218_follow_up.sql", "230_funil_teto_da_etapa.sql",
                     "232_funil_saidas_da_etapa.sql", "233_funil_toques_da_etapa.sql",
                     "235_motivos_de_perda_da_conta.sql", "236_reativar_o_lead_que_volta.sql",
                     "238_etapa_sai_do_quadro.sql", "254_funil_semeado_de.sql",
                     "282_perdido_automatico.sql"):
            c.execute((MIG / nome).read_text(encoding="utf-8"))
        for ch, o in (("novo", 0), ("contatado", 10), ("proposta", 30), ("perdido", 910)):
            c.execute("insert into funil_etapas (conta_id, chave, rotulo, ordem) values (%s,%s,%s,%s)",
                      (CONTA, ch, ch.capitalize(), o))
        # a Prime: 7 dias e 2 renovações no Contatado. 'proposta' fica SEM teto.
        c.execute("""update funil_etapas set teto_dias=7, renovacoes_max=2
                      where conta_id=%s and chave='contatado'""", (CONTA,))
        c.commit()
    yield p
    p.close()


@pytest.fixture()
def c(pool):
    with pool.connection() as con:
        for t in ("funil_renovacoes", "funil_movimentos", "mensagens", "conversas",
                  "prospeccao", "membros", "funil_regua", "funil_motivos_perda"):
            con.execute(f"delete from {t}")
        con.execute("""insert into funil_regua (conta_id, perdido_modo, perdido_toques_min)
                       values (%s,'ligado',3)""", (CONTA,))
        con.commit()
        yield con
        con.rollback()


# ------------------------------------------------------------------ ajudantes

def _lead(c, *, etapa="contatado", dias=0):
    lid = c.execute("""insert into prospeccao (conta_id, empresa, status, estagio, criado_em)
                       values (%s,'Lead',%s,'lead',%s) returning id""",
                    (CONTA, etapa, AGORA - timedelta(days=dias))).fetchone()[0]
    c.execute("""insert into funil_movimentos (conta_id, prospeccao_id, de, para, criado_em)
                 values (%s,%s,'novo',%s,%s)""",
              (CONTA, lid, etapa, AGORA - timedelta(days=dias)))
    return lid


def _msg(c, lead, direcao, *, membro=None, minutos=0):
    conv = c.execute("select id from conversas where prospeccao_id=%s", (lead,)).fetchone()
    if not conv:
        conv = c.execute("""insert into conversas (conta_id, prospeccao_id, canal)
                            values (%s,%s,'whatsapp') returning id""", (CONTA, lead)).fetchone()
    c.execute("""insert into mensagens (conversa_id, direcao, membro_id, criado_em)
                 values (%s,%s,%s,%s)""",
              (conv[0], direcao, membro, AGORA - timedelta(minutes=60 - minutos)))


def _modo(c, modo, *, toques=3, desde=None):
    c.execute("""update funil_regua set perdido_modo=%s, perdido_toques_min=%s, perdido_desde=%s
                  where conta_id=%s""", (modo, toques, desde, CONTA))


# ------------------------------------------------------------------ a regra, pura

def test_cliente_esperando_nunca_fecha():
    """O portão que não tem exceção: 28 leads da Prime estavam assim."""
    for estado in ("ok", "avisar", "vencido", "esgotado"):
        assert fp.decidir(bola="cliente", estado=estado, toques=9, toques_min=3,
                          entrou=None, desde=None) == "cliente_esperando"


def test_dentro_do_prazo_nao_fecha():
    assert fp.decidir(bola="nossa", estado="ok", toques=9, toques_min=3,
                      entrou=None, desde=None) == "no_prazo"
    assert fp.decidir(bola="nossa", estado="avisar", toques=9, toques_min=3,
                      entrou=None, desde=None) == "no_prazo"


def test_passou_do_prazo_mas_sem_follow_up_nao_fecha():
    """122 dos 240 leads parados da Prime levaram UM toque. A empresa não tentou."""
    assert fp.decidir(bola="nossa", estado="vencido", toques=2, toques_min=3,
                      entrou=None, desde=None) == "sem_follow_up"


def test_passou_do_prazo_com_os_toques_fecha():
    for estado in ("vencido", "esgotado"):
        assert fp.decidir(bola="nossa", estado=estado, toques=3, toques_min=3,
                          entrou=None, desde=None) == "fecha"


def test_corte_por_data_segura_a_fila_velha():
    antes = datetime(2026, 8, 20, tzinfo=timezone.utc)
    depois = datetime(2026, 9, 5, tzinfo=timezone.utc)
    corte = date(2026, 9, 1)
    assert fp.decidir(bola="nossa", estado="vencido", toques=5, toques_min=3,
                      entrou=antes, desde=corte) == "antes_do_corte"
    assert fp.decidir(bola="nossa", estado="vencido", toques=5, toques_min=3,
                      entrou=depois, desde=corte) == "fecha"


# ------------------------------------------------------------------ a bola e os toques

def test_bola_por_id_e_nao_por_data(c):
    """Duas mensagens no mesmo minuto: quem tem id maior falou por último."""
    lead = _lead(c, dias=30)
    _msg(c, lead, "out", minutos=10)
    _msg(c, lead, "in", minutos=10)          # mesmo instante, id maior
    assert fp.bola_de(c, lead) == "cliente"


def test_toque_do_celular_conta_igual(c):
    """membro_id nulo é o eco do que o vendedor digitou no telefone."""
    lead = _lead(c, dias=30)
    _msg(c, lead, "in", minutos=1)
    _msg(c, lead, "out", membro=None, minutos=2)
    _msg(c, lead, "out", membro=7, minutos=3)
    assert fp.toques_de(c, lead) == 2


def test_lead_sem_conversa_nao_tem_toque(c):
    lead = _lead(c, dias=30)
    assert fp.toques_de(c, lead) == 0
    assert fp.bola_de(c, lead) == "nossa"


# ------------------------------------------------------------------ a passada

def _lead_fechavel(c, *, dias=30, toques=3):
    lead = _lead(c, dias=dias)
    _msg(c, lead, "in", minutos=1)
    for i in range(toques):
        _msg(c, lead, "out", minutos=2 + i)
    return lead


def test_etapa_sem_teto_nao_entra(c):
    """'proposta' não tem prazo — não há do que o lead ter passado."""
    lead = _lead_fechavel(c, dias=90)
    c.execute("update prospeccao set status='proposta' where id=%s", (lead,))
    assert [l for l in fp.elegiveis(c, CONTA, AGORA) if l["id"] == lead] == []


def test_modo_off_nao_olha_nada(c):
    _lead_fechavel(c)
    _modo(c, "off")
    r = fp.avaliar(c, CONTA, AGORA)
    assert r == {"modo": "off", "fechados": 0, "simulados": 0, "poupados": 0, "leads": []}


def test_observando_conta_e_nao_fecha(c):
    lead = _lead_fechavel(c)
    _modo(c, "observando")
    r = fp.avaliar(c, CONTA, AGORA)
    assert r["simulados"] == 1 and r["fechados"] == 0
    assert c.execute("select status from prospeccao where id=%s", (lead,)).fetchone()[0] == "contatado"


def test_ligado_fecha_com_motivo_e_historico(c):
    lead = _lead_fechavel(c)
    r = fp.avaliar(c, CONTA, AGORA)
    assert r["fechados"] == 1
    assert c.execute("select status from prospeccao where id=%s", (lead,)).fetchone()[0] == "perdido"
    # o motivo na ficha
    motivo, etapa = c.execute(
        "select perda_motivo, perda_etapa from prospeccao where id=%s", (lead,)).fetchone()
    assert motivo == fp.MOTIVO_PERDA and etapa == "contatado"
    # e a linha do histórico, com autor que NÃO é 'manual'
    de, para, mot = c.execute(
        """select de, para, motivo from funil_movimentos
            where prospeccao_id=%s and para='perdido'""", (lead,)).fetchone()
    assert (de, para, mot) == ("contatado", "perdido", fp.MOTIVO_MOV)


def test_ligado_poupa_quem_esta_esperando(c):
    lead = _lead(c, dias=30)
    _msg(c, lead, "out", minutos=1)
    _msg(c, lead, "in", minutos=5)           # o cliente escreveu por último
    r = fp.avaliar(c, CONTA, AGORA)
    assert r["fechados"] == 0 and r["poupados"] == 1
    assert c.execute("select status from prospeccao where id=%s", (lead,)).fetchone()[0] == "contatado"


def test_um_toque_a_menos_nao_fecha(c):
    lead = _lead_fechavel(c, toques=2)
    assert fp.avaliar(c, CONTA, AGORA)["fechados"] == 0
    assert c.execute("select status from prospeccao where id=%s", (lead,)).fetchone()[0] == "contatado"


def test_corte_por_data_na_passada(c):
    velho = _lead_fechavel(c, dias=40)       # entrou em 09/08
    novo = _lead_fechavel(c, dias=10)        # entrou em 08/09
    _modo(c, "ligado", desde=date(2026, 9, 1))
    r = fp.avaliar(c, CONTA, AGORA)
    assert r["fechados"] == 1
    assert c.execute("select status from prospeccao where id=%s", (velho,)).fetchone()[0] == "contatado"
    assert c.execute("select status from prospeccao where id=%s", (novo,)).fetchone()[0] == "perdido"


def test_pessoa_ganha_da_regra(c):
    """Se alguém moveu o lead entre a leitura e o fechamento, a regra se cala."""
    lead = _lead_fechavel(c)
    alvo = [l for l in fp.elegiveis(c, CONTA, AGORA) if l["id"] == lead][0]
    c.execute("update prospeccao set status='proposta' where id=%s", (lead,))
    assert fp.fechar(c, CONTA, alvo, AGORA) is False
    assert c.execute("select status from prospeccao where id=%s", (lead,)).fetchone()[0] == "proposta"


def test_renovacao_adia_o_fechamento(c):
    """Renovar é o vendedor dizendo 'ainda estou nesse'. 10 dias com 1 renovação
    ainda está dentro do segundo período de 7."""
    lead = _lead_fechavel(c, dias=10)
    c.execute("""insert into funil_renovacoes (conta_id, prospeccao_id, etapa, membro_id, criado_em)
                 values (%s,%s,'contatado',NULL,%s)""", (CONTA, lead, AGORA - timedelta(days=1)))
    assert fp.avaliar(c, CONTA, AGORA)["fechados"] == 0
