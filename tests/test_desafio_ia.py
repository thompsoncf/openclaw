"""O desafio: a IA do número × a equipe (finance/desafio_ia.py, etapa 4).

O que cada teste segura:
* as colunas: a IA (o dono da regra com a IA ligada) primeiro, depois a equipe;
* as mesmas medidas, por lead recebido NO MÊS (Brasília), pra todos;
* a 1ª resposta conta a da IA (autor bot) — a régua do Raio-X só conta gente;
* qualificado = data E convidados; visita pela régua única; orçamento só o que chegou;
* o custo da IA por lead e por contrato (ia_uso, migração 394), com a régua de preço
  do produto;
* por que a IA chamou gente, e quanto a equipe levou pra responder;
* a equipe fora do horário comercial.
"""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from psycopg_pool import ConnectionPool

from finance import agenda as ag
from finance import desafio_ia as dia
from finance import ia_uso

BASE = Path(__file__).resolve().parents[1] / "db" / "migracoes"
BRT = ag.BRT
EMPRESA, CHIP2 = 34, 36

_SQL = """
create table nichos (id bigserial primary key, slug text, nome text);
create table contas (id bigint primary key, nome text, chip_de bigint, nicho_id bigint);
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text, ativo boolean default true, whatsapp text);
create table prospeccao (id bigserial primary key, conta_id bigint, vendedor_id bigint,
  evento_em date, evento_convidados int, orcamento_id bigint, criado_em timestamptz default now());
create table conversas (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  canal text default 'whatsapp', chip_id bigint, agente_ativo boolean default true,
  status text default 'aberta');
create table mensagens (id bigserial primary key, conversa_id bigint, canal text,
  direcao text, autor text, texto text, membro_id bigint, provider_sid text,
  status text, criado_em timestamptz default now());
create table eventos_agenda (id bigserial primary key, conta_id bigint, membro_id bigint,
  titulo text, inicio timestamptz, status text default 'ativo', tipo text default 'empresa',
  prospeccao_id bigint, tipo_evento text);
create table orcamentos (id bigserial primary key, conta_id bigint, status text);
create table orcamento_envios (id bigserial primary key, orcamento_id bigint, conta_id bigint, ok boolean);
create table contratos (id bigserial primary key, conta_id bigint, orcamento_id bigint,
  substitui_id bigint, assinado_em timestamptz, status text);
"""


@pytest.fixture()
def pool(monkeypatch):
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_desafio_ia_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity "
                  "where datname=%s and pid <> pg_backend_pid()", (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=4, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        c.execute("insert into contas (id, nome, chip_de) values (%s,'Prime',null),(%s,'CP',%s)",
                  (EMPRESA, CHIP2, EMPRESA))
        for m in ("388_regra_por_chip.sql", "390_ia_marca_visita.sql", "392_ia_orcamento.sql",
                  "394_ia_uso.sql"):
            c.execute((BASE / m).read_text(encoding="utf-8"))
        c.commit()
    # a Prime vende festa: visita é só o título "Visita…"
    from finance import visita as vis
    monkeypatch.setattr(vis, "vende_festa", lambda *a, **k: True)
    yield p
    p.close()


@pytest.fixture()
def prime(pool):
    with pool.connection() as c:
        ids = {n: c.execute("insert into membros (conta_id, nome, papel) values (%s,%s,'vendedor') "
                            "returning id", (EMPRESA, n)).fetchone()[0]
               for n in ("zaq teste", "Jacqueline", "Pedro")}
        c.execute("""insert into chip_regra (conta_id, chip_id, membro_id, ia_ligada)
                     values (%s,%s,%s,true)""", (EMPRESA, CHIP2, ids["zaq teste"]))
        c.commit()
    return ids


MES = "2026-09"
# quarta 16/09/2026 às 10h e às 22h (Brasília)
DIA_10 = datetime(2026, 9, 16, 10, tzinfo=BRT)
DIA_22 = datetime(2026, 9, 16, 22, tzinfo=BRT)


def _lead(pool, membro, *, chega=DIA_10, resposta_min=None, autor="humano", data=False,
          convidados=False, visita=False, orc=None, contrato=False):
    with pool.connection() as c:
        oid = None
        if orc:
            oid = c.execute("insert into orcamentos (conta_id, status) values (%s,%s) returning id",
                            (EMPRESA, orc)).fetchone()[0]
            if contrato:
                c.execute("""insert into contratos (conta_id, orcamento_id, assinado_em, status)
                             values (%s,%s,now(),'assinado')""", (EMPRESA, oid))
        lead = c.execute("""insert into prospeccao (conta_id, vendedor_id, evento_em, evento_convidados,
                                                    orcamento_id, criado_em)
                            values (%s,%s,%s,%s,%s,%s) returning id""",
                         (EMPRESA, membro, "2027-03-13" if data else None, 90 if convidados else None,
                          oid, chega)).fetchone()[0]
        conv = c.execute("insert into conversas (conta_id, prospeccao_id) values (%s,%s) returning id",
                         (EMPRESA, lead)).fetchone()[0]
        c.execute("insert into mensagens (conversa_id, direcao, autor, texto, criado_em) "
                  "values (%s,'in','lead','oi',%s)", (conv, chega))
        if resposta_min is not None:
            c.execute("insert into mensagens (conversa_id, direcao, autor, texto, criado_em) "
                      "values (%s,'out',%s,'olá',%s)",
                      (conv, autor, chega + timedelta(minutes=resposta_min)))
        if visita:
            c.execute("""insert into eventos_agenda (conta_id, titulo, inicio, prospeccao_id)
                         values (%s,'Visita — Ana',%s,%s)""", (EMPRESA, chega + timedelta(days=2), lead))
        c.commit()
    return lead, conv


def _col(d, nome):
    return next(c for c in d["colunas"] if c["nome"] == nome)


def test_a_ia_primeiro_e_as_mesmas_medidas_pra_todos(pool, prime):
    z, j = prime["zaq teste"], prime["Jacqueline"]
    _lead(pool, z, resposta_min=0.3, autor="bot", data=True, convidados=True, visita=True,
          orc="enviado", contrato=True)
    _lead(pool, z, resposta_min=0.5, autor="bot", data=True)
    _lead(pool, j, resposta_min=40)
    _lead(pool, j, resposta_min=3, orc="rascunho")          # rascunho que não saiu não conta
    _lead(pool, j)                                           # sem resposta
    d = dia.dados(pool, EMPRESA, MES)
    assert [c["nome"] for c in d["colunas"]][:1] == ["zaq teste"] and d["colunas"][0]["ia"]
    z_ = _col(d, "zaq teste")
    assert (z_["leads"], z_["resp_mediana_min"], z_["resp_5min_pct"]) == (2, 0.4, 100)
    assert (z_["qualif"], z_["visitas"], z_["orcamentos"], z_["contratos"]) == (1, 1, 1, 1)
    j_ = _col(d, "Jacqueline")
    assert (j_["leads"], j_["resp_mediana_min"], j_["resp_5min_pct"]) == (3, 21.5, 33)
    assert j_["orcamentos"] == 0


def test_so_leads_do_mes_em_horario_de_brasilia(pool, prime):
    j = prime["Jacqueline"]
    # 30/09 às 22h em Brasília já é 01/10 em UTC: é de setembro
    _lead(pool, j, chega=datetime(2026, 9, 30, 22, tzinfo=BRT), resposta_min=1)
    _lead(pool, j, chega=datetime(2026, 10, 1, 9, tzinfo=BRT), resposta_min=1)
    assert _col(dia.dados(pool, EMPRESA, MES), "Jacqueline")["leads"] == 1
    assert _col(dia.dados(pool, EMPRESA, "2026-10"), "Jacqueline")["leads"] == 1


def test_o_custo_da_ia_por_lead_e_por_contrato(pool, prime):
    z = prime["zaq teste"]
    lead, conv = _lead(pool, z, resposta_min=0.2, autor="bot", orc="enviado", contrato=True)
    _lead(pool, z, resposta_min=0.2, autor="bot")
    uso = SimpleNamespace(input_tokens=10_000, cache_creation_input_tokens=0,
                          cache_read_input_tokens=0, output_tokens=1_000)
    # Sonnet 4.6: 10k × 3 + 1k × 15 = US$ 0,045 × 5,40 = R$ 0,243
    assert ia_uso.custo_centavos("claude-sonnet-4-6", uso) == 24
    assert ia_uso.custo_centavos("modelo-novo", uso) == 24          # sem preço: o mais caro
    for _ in range(4):
        ia_uso.registrar(pool, EMPRESA, conv, lead, "claude-sonnet-4-6", SimpleNamespace(usage=uso))
    with pool.connection() as c:
        c.execute("update ia_uso set criado_em=%s", (DIA_10,))
        c.commit()
    d = dia.dados(pool, EMPRESA, MES)
    assert (d["custo_centavos"], d["custo_por_lead"], d["custo_por_contrato"]) == (96, 48, 96)


def test_por_que_a_ia_chamou_gente_e_quanto_a_equipe_levou(pool, prime):
    z = prime["zaq teste"]
    lead, conv = _lead(pool, z, resposta_min=0.2, autor="bot")
    lead2, conv2 = _lead(pool, z, resposta_min=0.2, autor="bot")
    with pool.connection() as c:
        c.execute("""insert into ia_avisos (conta_id, conversa_id, prospeccao_id, motivo, criado_em)
                     values (%s,%s,%s,'desconto',%s),(%s,%s,%s,'desconto',%s)""",
                  (EMPRESA, conv, lead, DIA_10, EMPRESA, conv2, lead2, DIA_10))
        c.execute("""insert into mensagens (conversa_id, direcao, autor, texto, criado_em)
                     values (%s,'out','humano','oi, é o Manoel',%s)""", (conv, DIA_10 + timedelta(minutes=30)))
        c.commit()
    av = dia.dados(pool, EMPRESA, MES)["avisos"]
    assert av == [{"motivo": "Pediu desconto", "n": 2, "agiu": 1, "mediana_min": 30.0}]


def test_a_equipe_fora_do_horario_comercial(pool, prime):
    j, p = prime["Jacqueline"], prime["Pedro"]
    _lead(pool, j, chega=DIA_22, resposta_min=300)
    _lead(pool, p, chega=DIA_22, resposta_min=4)
    _lead(pool, p, chega=DIA_10, resposta_min=2)
    _lead(pool, prime["zaq teste"], chega=DIA_22, resposta_min=0.2, autor="bot")   # a IA não entra
    f = dia.dados(pool, EMPRESA, MES)["fora"]
    assert f == {"leads_pct": 67, "mediana_min": 152.0, "em5_pct": 50}


def test_sem_ia_ligada_o_painel_diz_e_nao_quebra(pool, prime):
    with pool.connection() as c:
        c.execute("update chip_regra set ia_ligada=false")
        c.commit()
    d = dia.dados(pool, EMPRESA, MES)
    assert not d["tem_ia"] and d["custo_centavos"] is None and d["avisos"] == []


def test_mes_invalido_vira_o_atual():
    assert dia.dados.__defaults__ == (None,)
    assert dia.mes_atual() == dia.meses()[0]


def test_a_tela_renderiza(pool, prime):
    from web import painel_prospeccao as pp       # registra o template
    from web.portal import _env
    _lead(pool, prime["zaq teste"], resposta_min=0.3, autor="bot", visita=True)
    _lead(pool, prime["Jacqueline"], chega=DIA_22, resposta_min=40)
    d = dia.dados(pool, EMPRESA, MES)
    # o miolo da página (o base do painel pede a sessão inteira; o que muda é isto)
    tpl = pp._DESAFIO_TPL
    miolo = tpl[tpl.index('<div style="display:flex;align-items:flex-start'):tpl.rindex("{% endblock %}")]
    html = _env.from_string(miolo).render(d=d, brl=pp.brl, dur=pp._dur)
    assert "Desafio: IA × equipe" in html and "zaq teste · IA" in html and "0,3 min" in html
