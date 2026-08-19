"""Aviso de aniversário do lead pro vendedor (finance/lembretes._aniversarios).

Roda no mesmo ticker de fundo do resumo da agenda (~2 min), mas FORA do laço de
`agenda_config`: o aniversário é do lead do vendedor e não depende de a conta ter
ligado lembrete de agenda.

O que este arquivo protege, em ordem de importância:

1. **O check constraint.** O dedup grava `tipo='aniversario'` em `lembretes_enviados`,
   e o CHECK da migração 101 não conhecia esse valor. Sem recriá-lo (migração 171), o
   insert levanta CheckViolation — que, como o `_primeira_vez` roda em conexão própria
   e sem try/except no chamador, aborta o laço de TODAS as contas naquele tick, não só
   o aniversário. É o incidente que a migração 128 documenta, com o 'aviso_convidado'.
2. **Quem entra e quem não entra**: só lead aberto, com dono, no dia certo.
3. **Uma vez por ano**: o ticker roda dezenas de vezes por hora.

O ENVIO em si é trocado por um espião (fixture `push`). Dois motivos, e o segundo é
que manda: (a) o alvo aqui é QUEM entra e QUANTAS vezes, não o transporte; (b) o
binding Rust do webpush estoura `pyo3_runtime.PanicException` neste contêiner, e
PanicException herda de BaseException — o `except Exception` do próprio `enviar_push`
não segura. Com o espião dá pra conferir também PRA QUEM o aviso vai e com que link,
que a chamada real não deixaria ver.
"""
import os
from datetime import date, datetime, timedelta, timezone

import pytest
from psycopg_pool import ConnectionPool

from finance import lembretes as lb

BRT = timezone(timedelta(hours=-3))

_SQL = """
create table contas (id bigserial primary key, nome text);
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text default 'vendedor', ativo boolean default true,
  cockpit_push_ativo boolean default true);
create table prospeccao (id bigserial primary key, conta_id bigint, vendedor_id bigint,
  empresa text, contato text, status text default 'novo', estagio text default 'lead',
  nascimento date, atualizado_em timestamptz default now());
create table push_assinaturas (id bigserial primary key, conta_id bigint, membro_id bigint,
  endpoint text unique, p256dh text, auth text);
create table agenda_config (conta_id bigint primary key, resumo_ativo boolean default false,
  hora_resumo int, aviso_antes_min int, avisar_convidados boolean default true,
  lembrete_ativo boolean default false);
create table eventos_agenda (id bigserial primary key, conta_id bigint, membro_id bigint,
  titulo text, inicio timestamptz, fim timestamptz, local text, descricao text,
  lembrete_min int, tipo text default 'pessoal', link_online text, desfecho text,
  status text default 'ativo', criado_em timestamptz default now(), prospeccao_id bigint,
  ics_token text, pre_reserva_ate timestamptz, sinal_centavos int);
-- o CHECK aqui é o da migração 171 (o da 101 não tinha 'aniversario')
create table lembretes_enviados (
  id bigserial primary key,
  conta_id bigint not null references contas(id) on delete cascade,
  tipo text not null check (tipo in ('resumo','aviso','aviso_convidado','aniversario')),
  chave text not null,
  enviado_em timestamptz not null default now(),
  unique (conta_id, tipo, chave));
"""


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    nome = "zaq_aniversario_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {nome}")
        c.execute(f"create database {nome}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + nome
    p = ConnectionPool(url, min_size=1, max_size=3, open=True,
                       kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        c.commit()
    yield p
    p.close()


@pytest.fixture(autouse=True)
def push(monkeypatch):
    """Troca o disparo real por um registro do que teria sido enviado."""
    enviados = []

    def _falso(pool, conta_id, membro_id, titulo, corpo, url="/cockpit", badge=None):
        enviados.append({"conta": conta_id, "membro": membro_id, "titulo": titulo,
                         "corpo": corpo, "url": url})
        return 1

    from finance import cockpit as ck
    monkeypatch.setattr(ck, "enviar_push", _falso)
    return enviados


def _conta(c, nome="Emp"):
    return c.execute("insert into contas (nome) values (%s) returning id", (nome,)).fetchone()[0]


def _vend(c, conta, nome="Rob"):
    return c.execute("insert into membros (conta_id, nome, email) values (%s,%s,%s) returning id",
                     (conta, nome, f"{nome}-{conta}@x.com")).fetchone()[0]


def _lead(c, conta, vend, nasc, *, contato="Cliente", status="novo"):
    return c.execute(
        """insert into prospeccao (conta_id, vendedor_id, contato, empresa, nascimento, status)
           values (%s,%s,%s,'Empresa',%s,%s) returning id""",
        (conta, vend, contato, nasc, status)).fetchone()[0]


def _as_8h(quando: date):
    return datetime(quando.year, quando.month, quando.day, 8, 0, tzinfo=BRT)


# ── o constraint, que é o que apaga o ticker inteiro se faltar ─────────────
def test_o_tipo_aniversario_e_aceito_em_lembretes_enviados(pool):
    """Se este falhar com CheckViolation, a migração 171 não recriou o CHECK — e o
    estrago não é 'o aniversário não sai': é o tick inteiro abortando na primeira
    conta que tiver aniversariante."""
    with pool.connection() as c:
        conta = _conta(c, "Check"); c.commit()
    assert lb._primeira_vez(pool, conta, "aniversario", "1:2026-08-18") is True
    assert lb._primeira_vez(pool, conta, "aniversario", "1:2026-08-18") is False


# ── quem entra ────────────────────────────────────────────────────────────
def test_avisa_quem_faz_aniversario_hoje(pool, push):
    hoje = date.today()
    with pool.connection() as c:
        conta = _conta(c, "Hoje"); v = _vend(c, conta)
        lead = _lead(c, conta, v, date(1990, hoje.month, hoje.day), contato="Bruna")
        c.commit()
    assert lb._aniversarios(pool, _as_8h(hoje)) == 1
    assert len(push) == 1
    aviso = push[0]
    assert aviso["membro"] == v, "o aviso é pro DONO do lead, não pra conta inteira"
    assert "Bruna" in aviso["corpo"]
    assert aviso["url"] == f"/cockpit/lead/{lead}", "abre o lead num toque"


def test_sai_uma_vez_so_por_mais_que_o_ticker_rode(pool):
    """O ticker roda a cada ~2 min: sem o dedup seriam 30 pushes por hora."""
    hoje = date.today()
    with pool.connection() as c:
        conta = _conta(c, "Dedup"); v = _vend(c, conta)
        _lead(c, conta, v, date(1985, hoje.month, hoje.day))
        c.commit()
    assert lb._aniversarios(pool, _as_8h(hoje)) == 1
    assert lb._aniversarios(pool, _as_8h(hoje)) == 0
    assert lb._aniversarios(pool, _as_8h(hoje)) == 0


# ── quem NÃO entra ────────────────────────────────────────────────────────
def test_nao_avisa_de_outro_dia_sem_dono_nem_de_lead_fechado(pool):
    hoje = date.today()
    outro = hoje + timedelta(days=3)
    with pool.connection() as c:
        conta = _conta(c, "Nao"); v = _vend(c, conta)
        _lead(c, conta, v, date(1990, outro.month, outro.day), contato="Outro dia")
        # sem dono: não há pra quem mandar
        c.execute("""insert into prospeccao (conta_id, vendedor_id, contato, empresa, nascimento)
                     values (%s, null, 'Órfão', 'X', %s)""",
                  (conta, date(1990, hoje.month, hoje.day)))
        # parabenizar quem foi dado como perdido não é lembrete, é constrangimento
        _lead(c, conta, v, date(1990, hoje.month, hoje.day), contato="Perdido", status="perdido")
        _lead(c, conta, v, date(1990, hoje.month, hoje.day), contato="Ganho", status="ganho")
        _lead(c, conta, v, None, contato="Sem data")
        c.commit()
    assert lb._aniversarios(pool, _as_8h(hoje)) == 0


def test_so_dispara_na_hora_marcada(pool):
    """Meia-noite chegaria com o vendedor dormindo e viraria notificação velha."""
    hoje = date.today()
    with pool.connection() as c:
        conta = _conta(c, "Hora"); v = _vend(c, conta)
        _lead(c, conta, v, date(1990, hoje.month, hoje.day))
        c.commit()
    for h in (0, 7, 9, 23):
        assert lb._aniversarios(pool, _as_8h(hoje).replace(hour=h)) == 0
    assert lb._aniversarios(pool, _as_8h(hoje)) == 1


def test_29_de_fevereiro_nao_estoura(pool):
    """Data que só existe em ano bissexto: o filtro é por dia/mês, então em ano comum
    ela simplesmente não casa com nenhum dia — não pode levantar."""
    hoje = date.today()
    with pool.connection() as c:
        conta = _conta(c, "Bissexto"); v = _vend(c, conta)
        _lead(c, conta, v, date(2000, 2, 29), contato="Bissexta")
        c.commit()
    esperado = 1 if (hoje.month, hoje.day) == (2, 29) else 0
    assert lb._aniversarios(pool, _as_8h(hoje)) == esperado


# ── integração com o ticker ───────────────────────────────────────────────
def test_rodar_conta_o_aniversario_no_retorno(pool):
    """`rodar` é o que o poller do web chama; a chave nova tem que existir sempre,
    inclusive no caminho de falha (senão quem lê o retorno leva KeyError)."""
    hoje = date.today()
    with pool.connection() as c:
        conta = _conta(c, "Ticker"); v = _vend(c, conta)
        _lead(c, conta, v, date(1970, hoje.month, hoje.day))
        c.commit()
    r = lb.rodar(pool, _as_8h(hoje))
    assert r["aniversario"] == 1
    assert "resumo" in r and "aviso" in r
