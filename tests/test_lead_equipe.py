"""Vendedor que manda mensagem pro WhatsApp da empresa NÃO vira lead.

Qualquer número desconhecido que mandasse mensagem virava lead automaticamente —
inclusive o da própria equipe. Medido em produção: 4 membros cadastrados como lead
da empresa deles (um com 1150 mensagens na conversa), cada um com um colega
atribuído como "responsável".

A conversa continua entrando: o inbox é o lugar dela, e sumir com mensagem seria
pior que o lead errado. O que não nasce é o lead.

Banco dedicado e descartável com o schema mínimo que a rota usa (mesmo padrão do
teste de blindagem) — não replica migrações antigas nem toca o banco compartilhado.
"""
import os

import pytest
from psycopg_pool import ConnectionPool

from web.painel_prospeccao import _eh_numero_da_equipe, _wa_inbound_conversa

_SQL = """
create table prospeccao (id bigserial primary key, conta_id bigint, vendedor_id bigint,
  empresa text not null, contato text, whatsapp text, telefone text, tipo text default 'pj',
  origem text, temperatura text default 'frio', status text default 'novo',
  estagio text default 'base', atualizado_em timestamptz default now(),
  criado_em timestamptz default now());
create table conversas (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  canal text, contato_ref text, contato_nome text, status text default 'aberta',
  agente_ativo boolean default false, janela_expira_em timestamptz,
  chip_id bigint, visto_ate_id bigint, ultima_msg_em timestamptz default now());
create table mensagens (id bigserial primary key, conversa_id bigint, canal text,
  direcao text, autor text, texto text, provider_sid text,
  midia_ref jsonb, midia_tipo text, midia_meta jsonb,
  criado_em timestamptz default now());
-- dedup por CONVERSA + sid (migração 159): o id do WhatsApp é o mesmo nas duas pontas
create unique index ux_msg_sid on mensagens (conversa_id, provider_sid) where provider_sid is not null;
create table wa_contatos (conta_id bigint, numero8 text, nome text,
  da_agenda boolean default false, primary key (conta_id, numero8));
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text default 'vendedor', ativo boolean default true, whatsapp text,
  cockpit_pausado boolean default false);
-- o rodízio roda DENTRO da transação da entrada (distribuicao.atribuir_se_sem_dono).
-- Sem estas tabelas o erro aborta a transação inteira, o try/except do chamador
-- engole, e o commit vira rollback — o lead sumiria sem deixar rastro no teste.
create table distribuicao (conta_id bigint primary key, ativo boolean default false,
  ponteiro int default 0, avisar boolean default true, aviso_template_sid text,
  aviso_zap boolean not null default false, aviso_zap_chip_id bigint, aviso_zap_texto text,
  atualizado_em timestamptz default now());
create table distribuicao_fila (conta_id bigint, membro_id bigint, ordem int default 0,
  primary key (conta_id, membro_id));
"""

CONTA = 34
VENDEDOR = "86995454554"        # número do vendedor, como fica salvo em membros
CLIENTE = "5586994869921"       # número de um lead de verdade, como o WhatsApp entrega


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_lead_equipe_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        c.commit()
    yield p
    p.close()


def _membro(c, nome, numero, *, conta=CONTA, ativo=True):
    return c.execute("""insert into membros (conta_id, nome, whatsapp, ativo)
                        values (%s,%s,%s,%s) returning id""",
                     (conta, nome, numero, ativo)).fetchone()[0]


def _entrou(c, numero, texto="oi", sid=None, nome_perfil=""):
    """A rota devolve (conv_id, nova) desde a migração 159 — aqui só o id interessa."""
    conv_id, _nova = _wa_inbound_conversa(c, CONTA, numero, texto, sid, nome_perfil, True)
    return conv_id


def _leads(c):
    return c.execute("select empresa from prospeccao where conta_id=%s order by id",
                     (CONTA,)).fetchall()


# ------------------------------------------------------------------ a regra

def test_mensagem_do_vendedor_nao_vira_lead(pool):
    with pool.connection() as c:
        _membro(c, "PEDRO YAN PRIME", VENDEDOR)
        c.commit()
        conv = _entrou(c, "55" + VENDEDOR, "testando o zap da empresa", sid="s1")
        c.commit()
        assert _leads(c) == []                       # o funil não ganhou ninguém
        cv = c.execute("""select prospeccao_id, agente_ativo, canal from conversas where id=%s""",
                       (conv,)).fetchone()
    assert cv == (None, False, "whatsapp")           # conversa órfã, agente desligado


def test_a_mensagem_do_vendedor_continua_entrando(pool):
    """Sumir com a mensagem seria pior que o lead errado."""
    with pool.connection() as c:
        _membro(c, "PEDRO YAN PRIME", VENDEDOR)
        c.commit()
        conv = _entrou(c, "55" + VENDEDOR, "manda o contrato pra cliente", sid="s2")
        c.commit()
        msgs = c.execute("""select direcao, texto from mensagens where conversa_id=%s""",
                         (conv,)).fetchall()
    assert msgs == [("in", "manda o contrato pra cliente")]


def test_segunda_mensagem_do_vendedor_reusa_a_mesma_conversa(pool):
    with pool.connection() as c:
        _membro(c, "PEDRO YAN PRIME", VENDEDOR)
        c.commit()
        a = _entrou(c, "55" + VENDEDOR, "oi", sid="s3")
        c.commit()
        b = _entrou(c, "55" + VENDEDOR, "de novo", sid="s4")
        c.commit()
        n = c.execute("select count(*) from mensagens where conversa_id=%s", (a,)).fetchone()[0]
    assert a == b and n == 2 and _leads_vazio(pool)


def _leads_vazio(pool):
    with pool.connection() as c:
        return c.execute("select count(*) from prospeccao").fetchone()[0] == 0


# ------------------------------------------------------------------ o que NÃO muda

def test_cliente_de_verdade_continua_virando_lead(pool):
    with pool.connection() as c:
        _membro(c, "PEDRO YAN PRIME", VENDEDOR)
        c.commit()
        _entrou(c, CLIENTE, "quero orçar um evento", sid="s5", nome_perfil="Poly")
        c.commit()
        row = c.execute("""select empresa, contato, whatsapp, tipo, estagio, temperatura
                             from prospeccao where conta_id=%s""", (CONTA,)).fetchone()
    assert row == ("Poly", "Poly", "+" + CLIENTE, "pf", "lead", "quente")


def test_membro_de_OUTRA_conta_nao_protege_esta(pool):
    """O escopo por conta é sagrado: número que é da equipe da conta 99 é um
    desconhecido qualquer aqui — e vira lead normalmente."""
    with pool.connection() as c:
        _membro(c, "Vendedor de outra empresa", VENDEDOR, conta=99)
        c.commit()
        _entrou(c, "55" + VENDEDOR, "oi", sid="s6", nome_perfil="Fulano")
        c.commit()
        assert [r[0] for r in _leads(c)] == ["Fulano"]


def test_ex_funcionario_pode_virar_lead(pool):
    """Membro inativo não entra na regra: quem saiu da equipe pode virar cliente."""
    with pool.connection() as c:
        _membro(c, "EX VENDEDOR", VENDEDOR, ativo=False)
        c.commit()
        _entrou(c, "55" + VENDEDOR, "oi", sid="s7", nome_perfil="Ex Vendedor")
        c.commit()
        assert [r[0] for r in _leads(c)] == ["Ex Vendedor"]


def _orfa(c, numero):
    return c.execute("""insert into conversas (conta_id, prospeccao_id, canal, contato_ref)
                        values (%s,null,'whatsapp',%s) returning id""",
                     (CONTA, numero)).fetchone()[0]


def test_orfa_de_cliente_vira_lead_reusando_a_conversa(pool):
    """Guarda o comportamento ATUAL da órfã, que não é mais o de agosto: conversa
    reimportada do histórico deixou de ser beco sem saída — o contato entra no funil
    e a conversa que já existia é reaproveitada, em vez de nascer uma segunda."""
    with pool.connection() as c:
        conv = _orfa(c, CLIENTE)
        c.commit()
        mesma = _entrou(c, CLIENTE, "voltei", sid="s8")
        c.commit()
        assert mesma == conv                      # a mesma thread, não uma nova
        assert len(_leads(c)) == 1                # e agora com dono no funil


def test_orfa_de_vendedor_nao_vira_lead(pool):
    """E é justamente por isso que a regra da equipe entra ANTES da órfã: com o
    caminho novo, a conversa reimportada do próprio vendedor viraria lead dele."""
    with pool.connection() as c:
        _membro(c, "PEDRO YAN PRIME", VENDEDOR)
        conv = _orfa(c, "55" + VENDEDOR)
        c.commit()
        mesma = _entrou(c, "55" + VENDEDOR, "voltei", sid="s9")
        c.commit()
        assert mesma == conv
        assert _leads(c) == []


# ------------------------------------------------------------------ o casamento do número

def test_casa_pelos_8_ultimos_digitos(pool):
    """O 9 extra e o DDI não podem furar a regra — mesma chave do resto do módulo."""
    with pool.connection() as c:
        _membro(c, "PEDRO YAN PRIME", "86995454554")
        c.commit()
        assert _eh_numero_da_equipe(c, CONTA, "5586995454554") is True   # com DDI
        assert _eh_numero_da_equipe(c, CONTA, "+55 (86) 99545-4554") is True  # mascarado
        assert _eh_numero_da_equipe(c, CONTA, "95454554") is True        # só os 8
        assert _eh_numero_da_equipe(c, CONTA, "5586994869921") is False  # outro número
        assert _eh_numero_da_equipe(c, CONTA, "123") is False            # curto demais
        assert _eh_numero_da_equipe(c, CONTA, "") is False


def test_membro_sem_whatsapp_nao_casa_com_ninguem(pool):
    """Coluna vazia não pode virar curinga."""
    with pool.connection() as c:
        _membro(c, "MANOEL SOARES", None)
        _membro(c, "zaq teste", "")
        c.commit()
        assert _eh_numero_da_equipe(c, CONTA, CLIENTE) is False
