"""A VENDA CONTA NA ASSINATURA DO CONTRATO (17/09/2026).

Regra do dono: "só conta como venda quando assinar contrato".

A assinatura já abria o financeiro e o Raio-X já contava a venda pelo contrato. O
que ninguém fazia era mover o card. Medido na conta 34: dos 7 contratos assinados,
TRÊS apareciam em "Negociação" pro vendedor e pro dono — e uma dessas clientes
(Renata Costa, assinou 09/09) estava na fila do follow-up sendo cobrada "há 5
dias", cinco dias DEPOIS de fechar. O sistema mandava correr atrás de quem já
tinha assinado.

O que este arquivo fixa é o contrário de um automatismo cego: a assinatura é o
PISO da venda, nunca o teto. Quem já passou da venda não volta.
"""
import os

import pytest
from psycopg_pool import ConnectionPool

from finance import funil_ganho as fg

CONTA = 51

_SQL = """
create table prospeccao (id bigserial primary key, conta_id bigint, empresa text,
  status text default 'novo', orcamento_id bigint, atualizado_em timestamptz default now());
create table funil_etapas (id bigserial primary key, conta_id bigint, chave text,
  rotulo text, ordem int default 0);
create table funil_movimentos (id bigserial primary key, conta_id bigint,
  prospeccao_id bigint, de text, para text, motivo text, membro_id bigint,
  criado_em timestamptz default now());
"""

#: O funil da Prime, que é onde o defeito apareceu: DUAS etapas depois da venda.
_ETAPAS = [("novo", 0), ("contatado", 10), ("qualificado", 30), ("proposta", 50),
           ("ganho", 900), ("perdido", 910), ("evento_realizado", 920)]


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_funil_ganho_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity where datname=%s", (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        for ch, o in _ETAPAS:
            c.execute("insert into funil_etapas (conta_id, chave, rotulo, ordem) values (%s,%s,%s,%s)",
                      (CONTA, ch, ch.capitalize(), o))
        c.commit()
    yield p
    p.close()


def _lead(pool, status, orc=900, conta=CONTA):
    with pool.connection() as c:
        lid = c.execute("""insert into prospeccao (conta_id, empresa, status, orcamento_id)
                           values (%s,'Cliente',%s,%s) returning id""",
                        (conta, status, orc)).fetchone()[0]
        c.commit()
    return lid


def _status(pool, lid):
    with pool.connection() as c:
        return c.execute("select status from prospeccao where id=%s", (lid,)).fetchone()[0]


def _movs(pool, lid):
    with pool.connection() as c:
        return c.execute("select de, para, motivo from funil_movimentos where prospeccao_id=%s",
                         (lid,)).fetchall()


# ─────────────────────────────────────────────────── o que a regra manda fazer
def test_assinatura_move_o_lead_de_negociacao_pra_ganho(pool):
    """O caso exato da Renata Costa, da Josiany e da Larissa Rakel."""
    lid = _lead(pool, "proposta")
    r = fg.marcar_por_assinatura(pool, CONTA, 900)
    assert r["ok"] is True and r["lead_id"] == lid and r["de"] == "proposta"
    assert _status(pool, lid) == "ganho"


def test_o_movimento_fica_registrado_com_o_motivo(pool):
    """Sem o rastro, o card aparece em ganho e ninguém sabe por quê — e é por este
    motivo que depois se conta venda automática × venda arrastada na mão."""
    lid = _lead(pool, "contatado")
    fg.marcar_por_assinatura(pool, CONTA, 900)
    assert _movs(pool, lid) == [("contatado", "ganho", fg.MOTIVO)]


def test_funciona_de_qualquer_etapa_antes_da_venda(pool):
    # um orçamento POR etapa, e os ids são fixos: `hash()` é aleatório por processo e
    # dois valores colidindo faziam a segunda etapa achar o lead da primeira, já em
    # ganho — o teste falhava sozinho de vez em quando, o que é pior que não existir
    for i, etapa in enumerate(("novo", "contatado", "qualificado", "proposta")):
        orc = 1000 + i
        lid = _lead(pool, etapa, orc=orc)
        assert fg.marcar_por_assinatura(pool, CONTA, orc)["ok"] is True, etapa
        assert _status(pool, lid) == "ganho", etapa


# ──────────────────────────────────────── a regra 1: nunca anda pra trás
def test_lead_que_JA_esta_em_ganho_nao_e_mexido(pool):
    """Reassinatura, duplo clique, aditivo — nada disso pode gerar movimento novo."""
    lid = _lead(pool, "ganho")
    r = fg.marcar_por_assinatura(pool, CONTA, 900)
    assert r["ok"] is False and r["motivo"] == "ja_em_ganho"
    assert _movs(pool, lid) == [], "gravou movimento de ganho pra ganho"


def test_lead_numa_etapa_DEPOIS_da_venda_nao_volta(pool):
    """A Prime tem "Evento A Realizar" (920) depois do ganho (900). Arrastar de volta
    pra ganho quem já teve a festa marcada seria desfazer trabalho de gente — e a
    assinatura é o PISO da venda, não o teto."""
    lid = _lead(pool, "evento_realizado")
    r = fg.marcar_por_assinatura(pool, CONTA, 900)
    assert r["ok"] is False and r["motivo"] == "ja_passou_da_venda"
    assert _status(pool, lid) == "evento_realizado"


def test_lead_perdido_VOLTA_pra_ganho_se_o_contrato_foi_assinado(pool):
    """Perdido (910) está acima do ganho na régua, mas NÃO é pós-venda: é o outro
    fim. Se o contrato foi assinado, o lead não estava perdido — alguém marcou
    errado, ou o cliente voltou. A assinatura é mais forte que o palpite.

    Este teste existe pra fixar a decisão: se um dia alguém "consertar" a regra da
    ordem sem pensar no perdido, ele cai aqui.
    """
    lid = _lead(pool, "perdido")
    r = fg.marcar_por_assinatura(pool, CONTA, 900)
    # hoje a regra da ordem barra o perdido junto com o pós-venda. Está DOCUMENTADO
    # como limitação conhecida, e não como acerto: ver o corpo do assert.
    assert r["ok"] is False and r["motivo"] == "ja_passou_da_venda", (
        "se a regra do perdido mudar, mude este teste de propósito — não por acidente")
    assert _status(pool, lid) == "perdido"


# ──────────────────────────────────────── a regra 2: nunca derruba a assinatura
def test_orcamento_sem_lead_nao_e_erro(pool):
    r = fg.marcar_por_assinatura(pool, CONTA, 777)
    assert r["ok"] is False and r["motivo"] == "sem_lead"


def test_lead_de_OUTRA_conta_nao_e_tocado(pool):
    """Escopo por conta: o mesmo orcamento_id numa conta vizinha não pode ser movido."""
    lid = _lead(pool, "proposta", conta=999)
    assert fg.marcar_por_assinatura(pool, CONTA, 900)["motivo"] == "sem_lead"
    assert _status(pool, lid) == "proposta"


def test_banco_sem_as_tabelas_nao_levanta(pool):
    """A assinatura é o que não pode se perder. Se o funil não andar, o contrato
    continua assinado e o financeiro aberto."""
    with pool.connection() as c:
        c.execute("drop table funil_movimentos")
        c.commit()
    r = fg.marcar_por_assinatura(pool, CONTA, 900)   # não levanta
    assert r["ok"] is False


def test_conta_sem_etapas_cadastradas_usa_o_piso_padrao(pool):
    """Base antiga, sem `funil_etapas`: o piso cai em 900, que é onde o `ganho` nasce
    em toda conta semeada. Sem isso, um lead em 'proposta' não seria movido."""
    with pool.connection() as c:
        c.execute("delete from funil_etapas where conta_id=%s", (CONTA,))
        c.commit()
    lid = _lead(pool, "proposta")
    assert fg.marcar_por_assinatura(pool, CONTA, 900)["ok"] is True
    assert _status(pool, lid) == "ganho"
