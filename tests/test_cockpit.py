"""Cockpit do Vendedor (finance/cockpit): motor do app do vendedor.

- token mágico: gera, valida 1x, expira / não reusa;
- membro_por_email acha o vendedor ativo;
- leads_do_vendedor traz só os leads DELE, abertos, marcando IA vs sua vez;
- posse: não deixa mexer no lead de outro (mudar_etapa/fechar/assumir/mensagem);
- ações: mudar etapa, assumir (pausa o bot), fechar (sai da fila), enviar mensagem;
- pausar rodízio reflete no perfil.

Banco dedicado e descartável com o schema mínimo (mesmo padrão do teste de blindagem).
"""
import os
from datetime import datetime, timedelta, timezone

import pytest
from psycopg_pool import ConnectionPool

from finance import cockpit as ck

_BASE_SQL = """
create table contas (id bigserial primary key, nome text);
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text default 'vendedor', ativo boolean default true, whatsapp text,
  cockpit_push_ativo boolean default true, cockpit_pausado boolean default false);
create table prospeccao (id bigserial primary key, conta_id bigint, vendedor_id bigint,
  empresa text, cnpj text, segmento text, cidade text, uf text, contato text, cargo text,
  telefone text, whatsapp text, email text, status text default 'novo', temperatura text default 'frio',
  valor_estimado_centavos bigint default 0, origem text, obs text, instagram text, socio text,
  regime_tributario text, porte text, ultimo_contato_em timestamptz, proximo_contato_em timestamptz,
  orcamento_id bigint, tem_site boolean, maps_url text, receita text, site_url text,
  decisor_nome text, decisor_cargo text, decisor_telefone text, decisor_whatsapp boolean,
  decisor_em timestamptz, decisor_telefones jsonb, estagio text default 'lead',
  atualizado_em timestamptz default now(), criado_em timestamptz default now());
create table conversas (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  canal text, status text default 'aberta', agente_ativo boolean default true,
  responsavel_membro_id bigint, ultima_msg_em timestamptz default now(), criado_em timestamptz default now());
create table mensagens (id bigserial primary key, conversa_id bigint, canal text, direcao text,
  autor text default 'humano', membro_id bigint, texto text default '', provider_sid text,
  criado_em timestamptz default now());
create table funil_etapas (id bigserial primary key, conta_id bigint, chave text, rotulo text,
  ordem int default 0, fixa boolean default false, unique (conta_id, chave));
create table prospeccao_atividades (id bigserial primary key, prospeccao_id bigint, membro_id bigint,
  tipo text, resultado text, descricao text, criado_em timestamptz default now());
create table cockpit_acesso (token text primary key, conta_id bigint, membro_id bigint,
  expira_em timestamptz not null, usado_em timestamptz, criado_em timestamptz default now());
"""


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_cockpit_test"
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


def _conta(c, nome="Emp"):
    return c.execute("insert into contas (nome) values (%s) returning id", (nome,)).fetchone()[0]


def _membro(c, conta, nome="Rob", email=None, papel="vendedor", ativo=True):
    email = email or f"{nome.lower()}-{conta}@x.com"
    return c.execute("insert into membros (conta_id, nome, email, papel, ativo) values (%s,%s,%s,%s,%s) returning id",
                     (conta, nome, email, papel, ativo)).fetchone()[0]


def _lead(c, conta, vend, empresa="Padaria", status="novo", estagio="lead", wa="5586999990000"):
    return c.execute("""insert into prospeccao (conta_id, vendedor_id, empresa, status, estagio, whatsapp)
                        values (%s,%s,%s,%s,%s,%s) returning id""",
                     (conta, vend, empresa, status, estagio, wa)).fetchone()[0]


# ------------------------------------------------------------------ token mágico
def test_token_valida_e_reusa_na_janela(pool):
    with pool.connection() as c:
        conta = _conta(c); vend = _membro(c, conta); c.commit()
    tok = ck.gerar_token(pool, conta, vend)
    d = ck.validar_token(pool, tok)
    assert d and d["conta_id"] == conta and d["membro_id"] == vend and d["papel"] == "vendedor"
    # reusável dentro dos 15 min (scanner de e-mail não queima o link do vendedor)
    assert ck.validar_token(pool, tok) is not None
    with pool.connection() as c:
        assert c.execute("select usado_em from cockpit_acesso where token=%s", (tok,)).fetchone()[0] is not None


def test_token_expirado_nao_valida(pool):
    with pool.connection() as c:
        conta = _conta(c); vend = _membro(c, conta)
        c.execute("""insert into cockpit_acesso (token, conta_id, membro_id, expira_em)
                     values ('velho',%s,%s,%s)""",
                  (conta, vend, datetime.now(timezone.utc) - timedelta(minutes=1)))
        c.commit()
    assert ck.validar_token(pool, "velho") is None
    assert ck.validar_token(pool, "nao-existe") is None


def test_membro_por_email(pool):
    with pool.connection() as c:
        conta = _conta(c)
        vend = _membro(c, conta, email="vend@x.com")
        _membro(c, conta, nome="Fin", email="fin@x.com", papel="financeiro")
        c.commit()
    assert ck.membro_por_email(pool, "VEND@x.com") == {"conta_id": conta, "membro_id": vend}
    assert ck.membro_por_email(pool, "fin@x.com") is None      # financeiro não usa cockpit
    assert ck.membro_por_email(pool, "ninguem@x.com") is None


# ------------------------------------------------------------------ inbox / escopo
def test_leads_do_vendedor_escopo_e_abertos(pool):
    with pool.connection() as c:
        conta = _conta(c)
        v1 = _membro(c, conta, nome="V1", email="v1@x.com")
        v2 = _membro(c, conta, nome="V2", email="v2@x.com")
        aberto = _lead(c, conta, v1, "Aberto")
        _lead(c, conta, v1, "Ganho", status="ganho")           # fechado: fora
        _lead(c, conta, v1, "Base", estagio="base")            # ainda base: fora
        _lead(c, conta, v2, "DoOutro")                          # de outro vendedor: fora
        # conversa com bot ativo → IA
        c.execute("insert into conversas (conta_id, prospeccao_id, canal, agente_ativo) values (%s,%s,'whatsapp',true)",
                  (conta, aberto))
        c.commit()
    leads = ck.leads_do_vendedor(pool, conta, v1)
    assert [l["empresa"] for l in leads] == ["Aberto"]
    assert leads[0]["ia"] is True


def test_lead_do_vendedor_posse(pool):
    with pool.connection() as c:
        conta = _conta(c)
        v1 = _membro(c, conta, nome="V1", email="v1b@x.com")
        v2 = _membro(c, conta, nome="V2", email="v2b@x.com")
        meu = _lead(c, conta, v1, "Meu")
        alheio = _lead(c, conta, v2, "Alheio")
        c.commit()
    d = ck.lead_do_vendedor(pool, conta, v1, meu)
    assert d and d["empresa"] == "Meu"
    assert all(e["chave"] not in ("ganho", "perdido") for e in d["etapas"])   # etapas sem fechados
    assert ck.lead_do_vendedor(pool, conta, v1, alheio) is None               # não é dele


# ------------------------------------------------------------------ ações
def test_mudar_etapa(pool):
    with pool.connection() as c:
        conta = _conta(c); vend = _membro(c, conta, email="et@x.com")
        lead = _lead(c, conta, vend)
        c.execute("insert into funil_etapas (conta_id, chave, rotulo) values (%s,'qualificado','Qualificado')", (conta,))
        c.commit()
    assert ck.mudar_etapa(pool, conta, vend, lead, "qualificado")["ok"] is True
    assert ck.mudar_etapa(pool, conta, vend, lead, "ganho")["ok"] is False    # ganho = usar fechar
    with pool.connection() as c:
        assert c.execute("select status from prospeccao where id=%s", (lead,)).fetchone()[0] == "qualificado"


def test_fechar_sai_da_fila(pool):
    with pool.connection() as c:
        conta = _conta(c); vend = _membro(c, conta, email="fe@x.com")
        lead = _lead(c, conta, vend)
        c.commit()
    assert ck.fechar(pool, conta, vend, lead, "ganho")["ok"] is True
    assert ck.leads_do_vendedor(pool, conta, vend) == []                       # fechado sai do inbox
    with pool.connection() as c:
        assert c.execute("select status from prospeccao where id=%s", (lead,)).fetchone()[0] == "ganho"


def test_assumir_pausa_o_bot(pool):
    with pool.connection() as c:
        conta = _conta(c); vend = _membro(c, conta, email="as@x.com")
        lead = _lead(c, conta, vend)
        c.execute("insert into conversas (conta_id, prospeccao_id, canal, agente_ativo) values (%s,%s,'whatsapp',true)",
                  (conta, lead))
        c.commit()
    assert ck.assumir(pool, conta, vend, lead)["ok"] is True
    with pool.connection() as c:
        r = c.execute("select agente_ativo, responsavel_membro_id from conversas where prospeccao_id=%s",
                      (lead,)).fetchone()
    assert r[0] is False and r[1] == vend


def test_enviar_mensagem_grava_e_pausa(pool, monkeypatch):
    from finance import whatsapp_out as wo
    monkeypatch.setattr(wo, "enviar", lambda c, cid, num, txt: {"ok": True, "sid": "SM1"})
    with pool.connection() as c:
        conta = _conta(c)
        v1 = _membro(c, conta, nome="V1", email="m1@x.com")
        v2 = _membro(c, conta, nome="V2", email="m2@x.com")
        meu = _lead(c, conta, v1, "Meu")
        alheio = _lead(c, conta, v2, "Alheio")
        c.commit()
    assert ck.enviar_mensagem(pool, conta, v1, alheio, "oi")["ok"] is False    # não é dele
    assert ck.enviar_mensagem(pool, conta, v1, meu, "  ")["ok"] is False       # vazio
    assert ck.enviar_mensagem(pool, conta, v1, meu, "Olá!")["ok"] is True
    with pool.connection() as c:
        msg = c.execute("""select m.texto, m.direcao, cv.agente_ativo from mensagens m
                           join conversas cv on cv.id=m.conversa_id where cv.prospeccao_id=%s""", (meu,)).fetchone()
    assert msg[0] == "Olá!" and msg[1] == "out" and msg[2] is False


def test_pausar_rodizio_reflete_no_perfil(pool):
    with pool.connection() as c:
        conta = _conta(c); vend = _membro(c, conta, email="pa@x.com")
        c.commit()
    p0 = ck.perfil(pool, conta, vend)
    assert p0["pausado"] is False
    ck.set_pausado(pool, conta, vend, True)
    assert ck.perfil(pool, conta, vend)["pausado"] is True
