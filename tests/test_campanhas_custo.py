"""Métricas por canal da lista de campanhas (_campanhas_dados): agrega WhatsApp e
e-mail (enviados/respostas/erros), custo do WhatsApp e o alerta de teto de gasto.

Banco dedicado e descartável com o schema MÍNIMO que a consulta usa (mesmo padrão do
teste de blindagem) — não replica migrações antigas nem toca o banco compartilhado.
"""
import os

import pytest
from psycopg_pool import ConnectionPool

from web import painel_prospeccao as pp

_BASE_SQL = """
create table contas (id bigserial primary key, tipo text, nome text, chip_de bigint);
create table membros (id bigserial primary key, conta_id bigint, nome text, email text);
create table prospeccao (id bigserial primary key, conta_id bigint, estagio text,
  vendedor_id bigint);   -- dono do lead: escopo do KPI de conversa no chat
create table campanhas (id bigserial primary key, conta_id bigint, nome text, status text,
  limite_dia int default 40, wa_ativo boolean default false, wa_template_sid text,
  enviados_hoje int default 0, dia_contagem date, teto_wa numeric(10,2),
  responsavel_id bigint, wa_bloqueio text,
  criado_em timestamptz default now());
create table campanha_alvos (id bigserial primary key, campanha_id bigint, prospeccao_id bigint,
  status text, aberturas int, wa_status text, wa_custo numeric(10,4), proximo_envio_em timestamptz);
create table campanha_eventos (id bigserial primary key, campanha_id bigint, prospeccao_id bigint,
  canal text, evento text, detalhe text, quando timestamptz default now());
-- conversas/mensagens: os KPIs de "Gastos das campanhas" passaram a contar quem
-- ESCREVEU no chat (e quantos desses nunca receberam resposta humana), sinal que
-- os contadores de botão não pegam. Sem as tabelas aqui, _campanhas_dados quebra.
create table conversas (id bigserial primary key, conta_id bigint, prospeccao_id bigint, chip_id bigint);
create table mensagens (id bigserial primary key, conversa_id bigint, direcao text,
  autor text, texto text, criado_em timestamptz default now(),
  midia_ref jsonb, midia_tipo text, midia_meta jsonb);
create table canais_config (
  id bigserial primary key, conta_id bigint, canal text, identificador text,
  ativo boolean not null default true, token text, provedor text not null default 'twilio',
  wa_phone_id text);
create table app_config (chave text primary key, valor text not null,
  atualizado_em timestamptz not null default now());
"""


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_campanhas_custo_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_BASE_SQL)
        c.commit()
    yield p
    p.close()


def _lead(c, conta):
    return c.execute("insert into prospeccao (conta_id, estagio) values (%s,'lead') returning id",
                     (conta,)).fetchone()[0]


def test_dados_por_canal_e_custo(pool):
    with pool.connection() as c:
        conta = c.execute("insert into contas (tipo, nome) values ('pj','Custo A') returning id").fetchone()[0]
        camp = c.execute("insert into campanhas (conta_id, nome, status, teto_wa) values (%s,'C1','ativa',3.00) returning id",
                         (conta,)).fetchone()[0]
        p0, p1, p2 = _lead(c, conta), _lead(c, conta), _lead(c, conta)
        # WhatsApp: p0 respondeu, p1 enviado, p2 erro
        c.execute("insert into campanha_alvos (campanha_id, prospeccao_id, status, wa_status, wa_custo) values (%s,%s,'enviado','respondeu',0.3217)", (camp, p0))
        c.execute("insert into campanha_alvos (campanha_id, prospeccao_id, status, wa_status, wa_custo) values (%s,%s,'respondeu','enviado',0.3217)", (camp, p1))
        c.execute("insert into campanha_alvos (campanha_id, prospeccao_id, status, wa_status, wa_custo) values (%s,%s,'enviado','erro',null)", (camp, p2))
        c.execute("insert into campanha_eventos (campanha_id, prospeccao_id, canal, evento) values (%s,%s,'email','bounce')", (camp, p0))
        # p1 clicou "Agora não" duas vezes (não deve contar 2x) + p2 clicou um botão
        # diferente (não deve contar) — só distintos com detalhe='Agora não' entram.
        c.execute("insert into campanha_eventos (campanha_id, prospeccao_id, canal, evento, detalhe) values (%s,%s,'whatsapp','clicou','Agora não')", (camp, p1))
        c.execute("insert into campanha_eventos (campanha_id, prospeccao_id, canal, evento, detalhe) values (%s,%s,'whatsapp','clicou','Agora não')", (camp, p1))
        c.execute("insert into campanha_eventos (campanha_id, prospeccao_id, canal, evento, detalhe) values (%s,%s,'whatsapp','respondeu','Quero te conhecer')", (camp, p2))
        c.commit()
        camps, totais = pp._campanhas_dados(c, conta)
    cp = next(x for x in camps if x["id"] == camp)
    assert cp["wa_env"] == 2          # 'enviado' + 'respondeu' contam como enviada
    assert cp["wa_resp"] == 1
    assert cp["wa_err"] == 1
    assert abs(cp["gasto"] - 0.6434) < 1e-6
    assert cp["email"]["env"] == 2    # 2 alvos com status='enviado'
    assert cp["email"]["resp"] == 1
    assert cp["email"]["volta"] == 1  # 1 bounce
    assert cp["alerta"] == "ok"       # teto 3.00, gasto 0.64
    assert cp["teto"] == 3.0
    assert cp["virou"] == 3
    assert totais["msgs"] == 2
    assert totais["sem_interesse"] == 1  # p1 uma vez só (distinct), p2 não conta


def test_alerta_amarelo_e_coral_por_teto(pool):
    with pool.connection() as c:
        conta = c.execute("insert into contas (tipo, nome) values ('pj','Custo B') returning id").fetchone()[0]
        amar = c.execute("insert into campanhas (conta_id, nome, status, teto_wa) values (%s,'AM','ativa',0.38) returning id", (conta,)).fetchone()[0]
        pa = _lead(c, conta)
        c.execute("insert into campanha_alvos (campanha_id, prospeccao_id, wa_status, wa_custo) values (%s,%s,'enviado',0.3217)", (amar, pa))
        cor = c.execute("insert into campanhas (conta_id, nome, status, teto_wa) values (%s,'CO','ativa',0.50) returning id", (conta,)).fetchone()[0]
        pc = _lead(c, conta)
        c.execute("insert into campanha_alvos (campanha_id, prospeccao_id, wa_status, wa_custo) values (%s,%s,'enviado',0.6434)", (cor, pc))
        c.commit()
        camps, totais = pp._campanhas_dados(c, conta)
    by = {x["id"]: x for x in camps}
    assert by[amar]["alerta"] == "amar"   # 0.3217/0.38 = 85%
    assert by[cor]["alerta"] == "coral"   # 0.6434 >= 0.50
    assert by[cor]["pct"] == 100
    assert totais["perto"] == 2


def test_rota_campanhas_sem_query_obrigatorio():
    """Regressão: o decorator @router.get('/campanhas') precisa estar no handler
    prospeccao_campanhas (não numa helper) — senão a página vira 422 pedindo 'v'."""
    achou = {}
    for r in pp.router.routes:
        p = getattr(r, "path", "")
        if p in ("/painel/prospeccao/campanhas", "/painel/prospeccao/campanhas/metricas") \
                and "GET" in getattr(r, "methods", []):
            dep = getattr(r, "dependant", None)
            achou[p] = (r.endpoint.__name__, [q.name for q in dep.query_params] if dep else [])
    assert achou["/painel/prospeccao/campanhas"][0] == "prospeccao_campanhas"
    assert achou["/painel/prospeccao/campanhas"][1] == []   # nenhum query param obrigatório
    assert achou["/painel/prospeccao/campanhas/metricas"][0] == "prospeccao_campanhas_metricas"


def test_sem_teto_mostra_previsto(pool):
    with pool.connection() as c:
        conta = c.execute("insert into contas (tipo, nome) values ('pj','Custo C') returning id").fetchone()[0]
        camp = c.execute("insert into campanhas (conta_id, nome, status) values (%s,'ST','ativa') returning id", (conta,)).fetchone()[0]
        p0 = _lead(c, conta)
        c.execute("insert into campanha_alvos (campanha_id, prospeccao_id, wa_status, wa_custo) values (%s,%s,'enviado',0.3217)", (camp, p0))
        c.commit()
        camps, _ = pp._campanhas_dados(c, conta)
    cp = next(x for x in camps if x["id"] == camp)
    assert cp["alerta"] is None
    assert cp["teto"] is None
    assert cp["previsto_fmt"].startswith("R$")
