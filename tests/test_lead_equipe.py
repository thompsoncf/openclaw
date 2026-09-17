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
  origem text, origem_codigo text, temperatura text default 'frio', status text default 'novo',
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
  -- `whatsapp_id` é o número de quem se cadastrou PELO WhatsApp, e faltava aqui.
  -- Foi a coluna que denunciou o buraco de 17/09: a trava só lia `whatsapp`, e dos
  -- 9 leads de membro medidos em produção SEIS casavam só por este campo.
  whatsapp_id text,
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


def _membro(c, nome, numero, *, conta=CONTA, ativo=True, coluna="whatsapp"):
    """`coluna` escolhe ONDE o número mora: `whatsapp` (digitado no convite) ou
    `whatsapp_id` (cadastro pelo próprio WhatsApp). Os dois são do membro, e a trava
    tem que olhar os dois — ver `test_a_trava_vale_pro_numero_que_veio_do_cadastro`."""
    assert coluna in ("whatsapp", "whatsapp_id")
    return c.execute(f"""insert into membros (conta_id, nome, {coluna}, ativo)
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


# ═══════════════════════════════════════════ os dois buracos da trava (17/09/2026)
#
# A trava é de 04/09 e estava de pé. Em 17/09, medindo a produção pra responder outra
# pergunta do dono, apareceram NOVE leads de membro em CINCO contas, somando 4.309
# mensagens — três deles recebendo mensagem ainda naquela semana. A trava não estava
# quebrada: ela cobria menos do que parecia.

def test_a_trava_vale_pro_numero_que_veio_do_CADASTRO_e_nao_do_convite(pool):
    """BURACO 1: a checagem só lia `membros.whatsapp`.

    `whatsapp_id` é o número de quem se cadastrou pelo próprio WhatsApp, e dos 9 casos
    de produção SEIS casavam só por ele: o dono da conta 23 com 2.307 mensagens
    penduradas num lead do funil dele, a dona da 35 com 1.150, o da 37 com 180.
    """
    with pool.connection() as c:
        _membro(c, "MANOEL SOARES", VENDEDOR, coluna="whatsapp_id")
        c.commit()
        conv = _entrou(c, "55" + VENDEDOR, "vê esse orçamento aí", sid="w1")
        c.commit()
        assert _leads(c) == [], "número do cadastro virou lead — a trava olhou só o convite"
        assert c.execute("select prospeccao_id from conversas where id=%s",
                         (conv,)).fetchone()[0] is None


def test_os_dois_campos_valem_ao_mesmo_tempo(pool):
    """Um membro pode ter os DOIS preenchidos, com números diferentes — o do convite e
    o do cadastro. Os dois são dele, então nenhum dos dois vira lead."""
    outro = "86988887777"
    with pool.connection() as c:
        c.execute("""insert into membros (conta_id, nome, whatsapp, whatsapp_id, ativo)
                     values (%s,'DOIS NUMEROS',%s,%s,true)""", (CONTA, VENDEDOR, outro))
        c.commit()
        _entrou(c, "55" + VENDEDOR, "oi", sid="w2")
        _entrou(c, "55" + outro, "oi daqui também", sid="w3")
        c.commit()
        assert _leads(c) == []


def test_lead_ANTIGO_de_membro_para_de_ser_promovido_e_de_entrar_no_rodizio(pool):
    """BURACO 2, o pior: a trava morava dentro do `if not lead_id`.

    Quem virou lead ANTES de a trava existir — ou antes de entrar na equipe —
    continuava alimentando o funil pra sempre: cada mensagem promovia o lead de volta
    pra 'lead', esquentava, e podia cair no rodízio pra um COLEGA. Na Prime eram três
    vendedores assim, cada um dono do próprio lead, com 140 mensagens somadas.

    A mensagem continua entrando — é a regra deste arquivo inteiro. O que para é o
    funil tratar aquilo como cliente.
    """
    with pool.connection() as c:
        _membro(c, "THIAGO PINHEIRO", VENDEDOR)
        # o lead que já existia, do jeito que a produção tinha: em 'base', frio,
        # sem dono — e com a conversa já vinculada a ele
        lead = c.execute("""insert into prospeccao
                              (conta_id, empresa, whatsapp, estagio, status, temperatura)
                            values (%s,'Thiago Pinheiro',%s,'base','novo','frio')
                            returning id""", (CONTA, "+55" + VENDEDOR)).fetchone()[0]
        c.execute("""insert into conversas (conta_id, prospeccao_id, canal, contato_ref)
                     values (%s,%s,'whatsapp',%s)""", (CONTA, lead, "55" + VENDEDOR))
        c.commit()

        _entrou(c, "55" + VENDEDOR, "cliente pediu o contrato", sid="w4")
        c.commit()
        r = c.execute("""select estagio, temperatura, vendedor_id from prospeccao where id=%s""",
                      (lead,)).fetchone()
        msgs = c.execute("""select count(*) from mensagens ms join conversas cv
                              on cv.id = ms.conversa_id where cv.prospeccao_id=%s""",
                         (lead,)).fetchone()[0]
    assert r[0] == "base", "a mensagem do colega promoveu o lead dele pro funil"
    assert r[1] == "frio", "a mensagem do colega esquentou o lead dele"
    assert r[2] is None, "o lead do vendedor caiu no rodízio pra um colega"
    assert msgs == 1, "a mensagem se perdeu — e perder mensagem é pior que o lead errado"


def test_o_agente_nao_responde_o_colega_nem_na_conversa_que_ja_existia(pool):
    """A IA atender o próprio vendedor não é atendimento. Na conversa NOVA isso já
    valia (ela nasce com `agente_ativo=false`); na que já existia, a mensagem que
    chegava religava o agente junto com a janela de 24h."""
    with pool.connection() as c:
        _membro(c, "THIAGO PINHEIRO", VENDEDOR)
        lead = c.execute("""insert into prospeccao (conta_id, empresa, whatsapp, estagio)
                            values (%s,'Thiago',%s,'base') returning id""",
                         (CONTA, "+55" + VENDEDOR)).fetchone()[0]
        conv = c.execute("""insert into conversas
                              (conta_id, prospeccao_id, canal, contato_ref, agente_ativo)
                            values (%s,%s,'whatsapp',%s,false) returning id""",
                         (CONTA, lead, "55" + VENDEDOR)).fetchone()[0]
        c.commit()
        _entrou(c, "55" + VENDEDOR, "oi", sid="w5")   # o inbound chega com agente_on=True
        c.commit()
        on = c.execute("select agente_ativo from conversas where id=%s", (conv,)).fetchone()[0]
    assert on is False, "o agente-mestre religou a IA na conversa de um colega"


def test_cliente_de_verdade_com_lead_antigo_CONTINUA_sendo_promovido(pool):
    """O contrapeso: a trava não pode virar um freio pra cliente. Lead de cliente na
    base que responde continua sendo promovido e esquentando, como sempre foi."""
    with pool.connection() as c:
        _membro(c, "THIAGO PINHEIRO", VENDEDOR)
        lead = c.execute("""insert into prospeccao
                              (conta_id, empresa, whatsapp, estagio, status, temperatura)
                            values (%s,'Poly Festas',%s,'base','novo','frio')
                            returning id""", (CONTA, "+" + CLIENTE)).fetchone()[0]
        c.execute("""insert into conversas (conta_id, prospeccao_id, canal, contato_ref)
                     values (%s,%s,'whatsapp',%s)""", (CONTA, lead, CLIENTE))
        c.commit()
        _entrou(c, CLIENTE, "quero orçar", sid="w6")
        c.commit()
        r = c.execute("select estagio, temperatura from prospeccao where id=%s",
                      (lead,)).fetchone()
    assert r == ("lead", "quente")


def test_lead_marcado_como_EQUIPE_nao_e_promovido_de_volta_pro_funil(pool):
    """`estagio='equipe'` é onde os leads de membro foram parar quando a produção foi
    limpa, em 17/09/2026 — três na conta 34. O valor não é mágico: o funil lista
    `estagio='lead'` e a Base lista `estagio='base'`, então 'equipe' fica fora das
    duas telas sem precisar de código novo, e a CONVERSA continua no inbox com as
    mensagens todas.

    O que este teste garante é que a mensagem seguinte não desfaz a limpeza. Sem a
    trava do lead existente, o primeiro "oi" do vendedor devolvia o card pro funil.
    """
    with pool.connection() as c:
        _membro(c, "THIAGO PINHEIRO", VENDEDOR)
        lead = c.execute("""insert into prospeccao (conta_id, empresa, whatsapp, estagio, status)
                            values (%s,'Thiago Pinheiro',%s,'equipe','contatado')
                            returning id""", (CONTA, "+55" + VENDEDOR)).fetchone()[0]
        c.execute("""insert into conversas (conta_id, prospeccao_id, canal, contato_ref)
                     values (%s,%s,'whatsapp',%s)""", (CONTA, lead, "55" + VENDEDOR))
        c.commit()
        _entrou(c, "55" + VENDEDOR, "bom dia", sid="w7")
        c.commit()
        r = c.execute("select estagio from prospeccao where id=%s", (lead,)).fetchone()[0]
        n = c.execute("""select count(*) from mensagens ms join conversas cv
                           on cv.id = ms.conversa_id where cv.prospeccao_id=%s""",
                      (lead,)).fetchone()[0]
    assert r == "equipe", "a mensagem do vendedor devolveu o lead dele pro funil"
    assert n == 1, "a mensagem se perdeu"
