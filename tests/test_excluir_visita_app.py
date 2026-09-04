"""Excluir a visita pelo celular — o corretivo de "marquei com o vendedor errado".

Pedido do dono, 04/09/2026: hoje não existe NENHUM jeito de corrigir o vendedor de
uma visita — nem no painel (Remarcar só muda data/hora, Excluir é só do dono), nem
no app (só Remarcar existe). A escolha, depois do mockup aprovado: em vez do dono
trocar o vendedor pra ele, o próprio vendedor apaga a visita errada e recria — mais
simples, e não abre um caminho novo de "editar quem marcou".

A trava é a MESMA de sempre (`ag.excluir_evento` / `ag.por_que_nao_exclui`), sem
exceção nenhuma pro app: orçamento, sinal, convidado ou mensagem enviada continuam
impedindo apagar — é a regra 0 (nada do cliente se perde), e ela vale aqui igual
vale no botão do painel.
"""
import os
from datetime import datetime, timedelta

import pytest
from psycopg_pool import ConnectionPool

from finance import agenda as ag
from finance import cockpit as ck

CONTA, VEND, OUTRO = 1, 10, 11

_SQL = """
create table contas (id bigserial primary key, nome text, nome_fantasia text,
  razao_social text, endereco text, bairro text, cidade text, uf text,
  cep text, telefone text);
create table membros (id bigserial primary key, conta_id bigint, nome text,
  papel text default 'vendedor', ativo boolean default true);
create table prospeccao (id bigserial primary key, conta_id bigint, vendedor_id bigint,
  empresa text, contato text, whatsapp text, telefone text, status text default 'novo',
  estagio text default 'lead', ultimo_contato_em timestamptz,
  atualizado_em timestamptz default now());
create table prospeccao_atividades (id bigserial primary key, prospeccao_id bigint,
  membro_id bigint, tipo text, resultado text, descricao text,
  criado_em timestamptz default now());
create table eventos_agenda (id bigserial primary key, conta_id bigint, membro_id bigint,
  titulo text, inicio timestamptz, fim timestamptz, local text, descricao text,
  lembrete_min int, tipo text default 'pessoal', link_online text, desfecho text,
  status text default 'ativo', criado_em timestamptz default now(), prospeccao_id bigint,
  ics_token text, pre_reserva_ate timestamptz, sinal_centavos int,
  tipo_evento text, convidados int, hora_sugerida boolean default false);
create table orcamentos (id bigserial primary key, conta_id bigint,
  evento_agenda_id bigint);
create table evento_convidados (id bigserial primary key,
  evento_id bigint not null references eventos_agenda(id) on delete cascade,
  nome text);
create table agenda_mensagens_log (id bigserial primary key,
  evento_id bigint references eventos_agenda(id) on delete cascade, tipo text);
"""


@pytest.fixture()
def pool():
    dbname = "zaq_excluir_visita"
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1,
                           open=True, kwargs={"autocommit": True, "prepare_threshold": None})
    with admin.connection() as c:
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity "
                  "where datname=%s and pid <> pg_backend_pid()", (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True,
                       kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        c.execute("insert into contas (id, nome, nome_fantasia, endereco) "
                  "values (%s,'Prime','Salão Prime','Rua X, 100')", (CONTA,))
        c.execute("insert into membros (id, conta_id, nome) values (%s,%s,'Pedro Yan'), "
                  "(%s,%s,'Jacqueline')", (VEND, CONTA, OUTRO, CONTA))
        c.commit()
    yield p
    p.close()


def _daqui(dias, hora=15):
    return (datetime.now(ag.BRT) + timedelta(days=dias)).replace(
        hour=hora, minute=0, second=0, microsecond=0)


def _lead(pool, *, vend=VEND, nome="Camila", wa="5586999990000"):
    with pool.connection() as c:
        lid = c.execute("insert into prospeccao (conta_id, vendedor_id, contato, whatsapp) "
                        "values (%s,%s,%s,%s) returning id",
                        (CONTA, vend, nome, wa)).fetchone()[0]
        c.commit()
    return lid


def _visita(pool, *, dias=5, lead=None, membro=VEND, status="ativo", sinal=None):
    ini = _daqui(dias)
    with pool.connection() as c:
        eid = c.execute(
            """insert into eventos_agenda (conta_id, membro_id, titulo, inicio, fim,
                 local, prospeccao_id, status, sinal_centavos, tipo)
               values (%s,%s,'Visita — Camila',%s,%s,'Salão Prime',%s,%s,%s,'empresa')
               returning id""",
            (CONTA, membro, ini, ini + timedelta(minutes=60), lead, status, sinal)).fetchone()[0]
        c.commit()
    return eid


def _existe(pool, eid):
    with pool.connection() as c:
        return c.execute("select 1 from eventos_agenda where id=%s", (eid,)).fetchone() is not None


# ═════════════════ 1 · o caminho feliz ═════════════════

def test_a_visita_apaga(pool):
    lead = _lead(pool)
    eid = _visita(pool, lead=lead)
    r = ck.excluir_visita(pool, CONTA, VEND, eid)
    assert r == {"ok": True}
    assert not _existe(pool, eid)


def test_o_dono_titular_tambem_apaga(pool):
    """`membro_id=None` é o dono titular usando o app — sem posse pra checar."""
    lead = _lead(pool)
    eid = _visita(pool, lead=lead)
    r = ck.excluir_visita(pool, CONTA, None, eid)
    assert r["ok"] is True


# ═════════════════ 2 · só visita ═════════════════

def test_festa_sem_lead_nao_e_visita_pra_excluir(pool):
    """Sem lead pendurado não é visita — é festa, reunião, bloqueio de data. Isso
    continua só no painel, com o dono."""
    ev = ag.criar_evento(pool, CONTA, "Locação — Jonas", _daqui(5), membro_id=VEND)
    r = ck.excluir_visita(pool, CONTA, VEND, ev["id"])
    assert r["ok"] is False
    assert _existe(pool, ev["id"]), "a festa foi apagada pelo app"


def test_visita_cancelada_nao_existe_mais_pro_excluir(pool):
    lead = _lead(pool)
    eid = _visita(pool, lead=lead, status="cancelado")
    r = ck.excluir_visita(pool, CONTA, VEND, eid)
    assert r["ok"] is False
    assert _existe(pool, eid)


# ═════════════════ 3 · posse ═════════════════

def test_o_vendedor_nao_exclui_a_visita_de_outro(pool):
    lead = _lead(pool, vend=OUTRO)
    eid = _visita(pool, lead=lead, membro=OUTRO)
    r = ck.excluir_visita(pool, CONTA, VEND, eid)
    assert r["ok"] is False and r["erro"] == "escopo"
    assert _existe(pool, eid), "a visita de outro vendedor foi apagada"


def test_gestao_exclui_qualquer_uma(pool):
    lead = _lead(pool, vend=OUTRO)
    eid = _visita(pool, lead=lead, membro=OUTRO)
    r = ck.excluir_visita(pool, CONTA, VEND, eid, gestao=True)
    assert r["ok"] is True
    assert not _existe(pool, eid)


def test_nao_atravessa_conta(pool):
    lead = _lead(pool)
    eid = _visita(pool, lead=lead)
    r = ck.excluir_visita(pool, 999, VEND, eid)
    assert r["ok"] is False
    assert _existe(pool, eid)


# ═════════════════ 4 · a trava — nada do cliente pode se perder ═════════════════

def test_travado_por_orcamento(pool):
    lead = _lead(pool)
    eid = _visita(pool, lead=lead)
    with pool.connection() as c:
        c.execute("insert into orcamentos (conta_id, evento_agenda_id) values (%s,%s)",
                  (CONTA, eid))
        c.commit()
    r = ck.excluir_visita(pool, CONTA, VEND, eid)
    assert r["ok"] is False and r["erro"] == "trava"
    assert "orçamento" in r["msg"]
    assert _existe(pool, eid)


def test_travado_por_sinal(pool):
    lead = _lead(pool)
    eid = _visita(pool, lead=lead, sinal=15000)
    r = ck.excluir_visita(pool, CONTA, VEND, eid)
    assert r["ok"] is False and r["erro"] == "trava"
    assert "sinal" in r["msg"]
    assert _existe(pool, eid)


def test_travado_por_convidado(pool):
    lead = _lead(pool)
    eid = _visita(pool, lead=lead)
    with pool.connection() as c:
        c.execute("insert into evento_convidados (evento_id, nome) values (%s,'Ana')", (eid,))
        c.commit()
    r = ck.excluir_visita(pool, CONTA, VEND, eid)
    assert r["ok"] is False and r["erro"] == "trava"
    assert "convidado" in r["msg"]
    assert _existe(pool, eid)


def test_travado_por_mensagem(pool):
    lead = _lead(pool)
    eid = _visita(pool, lead=lead)
    with pool.connection() as c:
        c.execute("insert into agenda_mensagens_log (evento_id, tipo) values (%s,'convite')",
                  (eid,))
        c.commit()
    r = ck.excluir_visita(pool, CONTA, VEND, eid)
    assert r["ok"] is False and r["erro"] == "trava"
    assert "convite" in r["msg"]
    assert _existe(pool, eid)
