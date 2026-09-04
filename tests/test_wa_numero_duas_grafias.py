"""O mesmo número escrito de dois jeitos não pode virar duas conversas.

No Brasil o WhatsApp entrega o mesmo contato ora com 12 dígitos (55 + DDD + 8, o
formato antigo), ora com 13 (com o nono dígito). Os três caminhos que gravam
conversa de WhatsApp casavam por igualdade CRUA de `contato_ref` — então a mesma
pessoa aparecia duas vezes na caixa, cada thread com metade do que ela escreveu, e
o vendedor respondia na que estivesse por cima.

Onde doía cada um:

- **histórico** (importação do pareamento): traz justamente o formato antigo dos
  chats velhos; um re-pareamento duplicava a aba de quem já tinha virado lead.
- **entrada** (mensagem nova): a conversa que veio do histórico não era
  reconhecida, e a mensagem nova abria outra thread do mesmo cliente.
- **entrada pelo botão "Agora não"**: mesma busca, mesmo desfecho.

A regra é `_wa_equivalentes`: as duas grafias do MESMO número, e só isso — os
últimos 8 dígitos (atalho que o módulo usa pra achar lead) casariam o final de um
celular do 86 com o de um do 11, e aí a mensagem cairia na conversa de outra
pessoa. Tem teste pra isso aqui também.

Schema mínimo dos caminhos exercitados; nada de migração.
"""
import os

import pytest
from psycopg_pool import ConnectionPool

from web import painel_prospeccao as pp

CONTA = 11
NUM13 = "5586998392961"      # com o nono dígito
NUM12 = "558698392961"       # o MESMO número, do jeito antigo

_SQL = """
create table prospeccao (id bigserial primary key, conta_id bigint, vendedor_id bigint,
  empresa text, contato text, tipo text default 'pj',
  telefone text, whatsapp text, email text, origem text,
  status text default 'novo', temperatura text default 'frio', estagio text default 'base',
  atualizado_em timestamptz default now(), criado_em timestamptz default now());
create table conversas (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  canal text default 'whatsapp', contato_ref text, contato_nome text,
  status text default 'aberta', agente_ativo boolean default true,
  responsavel_membro_id bigint, janela_expira_em timestamptz,
  ultima_msg_em timestamptz default now(), criado_em timestamptz default now(), chip_id bigint, visto_ate_id bigint);
create table mensagens (id bigserial primary key, conversa_id bigint, canal text,
  direcao text, autor text, texto text, membro_id bigint, provider_sid text,
  status text, criado_em timestamptz default now(),
  midia_ref jsonb, midia_tipo text, midia_meta jsonb, midia_arquivo text, midia_guardada_em timestamptz, midia_guardada_por bigint);
create table wa_contatos (conta_id bigint, numero8 text, nome text,
  da_agenda boolean default false, primary key (conta_id, numero8));
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text, ativo boolean default true, cockpit_pausado boolean default false,
  whatsapp text);

-- unicidade por CONVERSA, não global: o id do WhatsApp é o mesmo nas duas pontas
-- da mensagem, e global fazia a conta que recebe perder a dela (migração 159)
create unique index if not exists idx_mensagens_sid_conversa
  on mensagens (conversa_id, provider_sid) where provider_sid is not null;
"""


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_duas_grafias_test"
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


def _conversas(c):
    return c.execute("select count(*) from conversas").fetchone()[0]


def _msgs(c, conv_id):
    return c.execute("select texto from mensagens where conversa_id=%s order by id",
                     (conv_id,)).fetchall()


def _historico(c, numero, texto, *, sid, de_mim=False):
    return pp._wa_historico_conversa(c, CONTA, numero, texto, sid, None, de_mim=de_mim)


def _entrada(c, numero, texto, *, sid, nome="Perfil"):
    # devolve (conversa, nova); aqui só a conversa interessa
    return pp._wa_inbound_conversa(c, CONTA, numero, texto, sid, nome, False)[0]


# --------------------------------------------------------------- histórico

def test_historico_reusa_conversa_da_outra_grafia(pool):
    """Chat antigo importado com 12 dígitos; a onda seguinte trouxe 13."""
    with pool.connection() as c:
        a = _historico(c, NUM12, "oi, tem bolo de pote?", sid="h1")
        b = _historico(c, NUM13, "quanto custa?", sid="h2")
        c.commit()
        assert a == b
        assert _conversas(c) == 1
        assert len(_msgs(c, a)) == 2


def test_historico_nao_duplica_a_aba_de_quem_ja_e_lead(pool):
    """Re-pareamento: a conversa já virou lead e não pode ganhar uma cópia órfã."""
    with pool.connection() as c:
        lead = c.execute(
            """insert into prospeccao (conta_id, empresa, whatsapp) values (%s,'Doce Mell',%s)
               returning id""", (CONTA, "+" + NUM13)).fetchone()[0]
        conv = c.execute(
            """insert into conversas (conta_id, prospeccao_id, contato_ref)
               values (%s,%s,%s) returning id""", (CONTA, lead, NUM13)).fetchone()[0]
        assert _historico(c, NUM12, "mensagem velha", sid="h1") == conv
        c.commit()
        assert _conversas(c) == 1


def test_historico_de_outro_ddd_com_final_igual_e_outra_conversa(pool):
    """A trava: 8 dígitos finais iguais, número diferente."""
    with pool.connection() as c:
        a = _historico(c, NUM13, "sou do 86", sid="h1")
        b = _historico(c, "5511998392961", "sou do 11", sid="h2")
        c.commit()
        assert a != b
        assert _conversas(c) == 2


# ------------------------------------------------------------------ entrada

def test_entrada_reusa_a_conversa_importada_na_outra_grafia(pool):
    """O caso que enchia a caixa de gente repetida: a conversa veio do histórico com
    12 dígitos e a mensagem nova chega com 13."""
    with pool.connection() as c:
        antiga = _historico(c, NUM12, "conversa de antes", sid="h1")
        nova = _entrada(c, NUM13, "oi, quero encomendar", sid="e1")
        c.commit()
        assert nova == antiga
        assert _conversas(c) == 1
        assert len(_msgs(c, nova)) == 2


def test_entrada_leva_a_conversa_importada_junto_pro_lead_novo(pool):
    """Achar a conversa antiga não basta: ela tem que ficar ligada ao lead que a
    mensagem nova criou, senão o histórico continua fora da ficha."""
    with pool.connection() as c:
        antiga = _historico(c, NUM12, "conversa de antes", sid="h1")
        _entrada(c, NUM13, "oi", sid="e1")
        c.commit()
        vinculo = c.execute("select prospeccao_id from conversas where id=%s",
                            (antiga,)).fetchone()[0]
        assert vinculo is not None


def test_entrada_herda_o_nome_da_conversa_importada(pool):
    """O nome que já estava na conversa importada é melhor que o pushName do momento —
    e antes ele se perdia junto com a conversa que não era encontrada."""
    with pool.connection() as c:
        _historico(c, NUM12, "oi", sid="h1", de_mim=False)
        c.execute("update conversas set contato_nome='Confeitaria Doce Mell' where contato_ref=%s",
                  (NUM12,))
        _entrada(c, NUM13, "oi de novo", sid="e1", nome="doce mell 🍰")
        c.commit()
        empresa = c.execute("select empresa from prospeccao order by id desc limit 1").fetchone()[0]
        assert empresa == "Confeitaria Doce Mell"


def test_entrada_nao_abre_thread_paralela_em_ficha_diferente(pool):
    """Duas fichas com o mesmo telefone: a busca acha a mais recente, a conversa está
    pendurada na outra. Exigir conversa órfã pra casar por número abria uma segunda."""
    with pool.connection() as c:
        antigo = c.execute(
            """insert into prospeccao (conta_id, empresa, whatsapp, atualizado_em)
               values (%s,'Ficha velha',%s, now() - interval '5 days') returning id""",
            (CONTA, "+" + NUM13)).fetchone()[0]
        c.execute(
            """insert into prospeccao (conta_id, empresa, whatsapp, atualizado_em)
               values (%s,'Ficha nova',%s, now())""", (CONTA, "+" + NUM13))
        conv = c.execute(
            """insert into conversas (conta_id, prospeccao_id, contato_ref)
               values (%s,%s,%s) returning id""", (CONTA, antigo, NUM13)).fetchone()[0]
        assert _entrada(c, NUM13, "oi", sid="e1") == conv
        c.commit()
        assert _conversas(c) == 1


def test_entrada_prefere_a_conversa_do_proprio_lead(pool):
    """Com uma órfã por perto, a mensagem vai pra conversa do lead que ela acabou de
    esquentar — é a que a ficha mostra."""
    with pool.connection() as c:
        lead = c.execute(
            """insert into prospeccao (conta_id, empresa, whatsapp) values (%s,'Doce Mell',%s)
               returning id""", (CONTA, "+" + NUM13)).fetchone()[0]
        orfa = c.execute(
            """insert into conversas (conta_id, contato_ref, ultima_msg_em)
               values (%s,%s, now()) returning id""", (CONTA, NUM12)).fetchone()[0]
        do_lead = c.execute(
            """insert into conversas (conta_id, prospeccao_id, contato_ref, ultima_msg_em)
               values (%s,%s,%s, now() - interval '2 days') returning id""",
            (CONTA, lead, NUM13)).fetchone()[0]
        assert _entrada(c, NUM13, "oi", sid="e1") == do_lead
        c.commit()
        assert _msgs(c, orfa) == []


def test_entrada_de_outro_ddd_cai_no_mesmo_lead_pelo_casamento_antigo(pool):
    """LIMITE CONHECIDO, e de propósito fora deste conserto.

    Na entrada, quem acha o LEAD é o casamento pelos últimos 8 dígitos — anterior a
    tudo isto e usado no módulo inteiro (campanha, Twilio, eco de saída, agenda).
    Achado o mesmo lead pros dois números, a conversa é a dele, e a regra das duas
    grafias não tem como desempatar: ela escolhe entre conversas, não entre fichas.
    Apertar aquele casamento é outro conserto — hoje ele é o que salva o lead
    cadastrado sem DDI, que não bate com nenhuma das duas grafias.

    Fica pinado pra o limite não passar por acidente: no histórico (sem lead no meio)
    os dois DDDs seguem separados, e é isso que o teste vizinho garante. Se um dia
    este cair, é melhora, não regressão."""
    with pool.connection() as c:
        a = _entrada(c, NUM13, "sou do 86", sid="e1")
        b = _entrada(c, "5511998392961", "sou do 11", sid="e2")
        c.commit()
        assert a == b
        assert c.execute("select count(*) from prospeccao").fetchone()[0] == 1


# --------------------------------------------- entrada pelo botão "Agora não"

def test_agora_nao_cai_na_conversa_que_ja_existia(pool):
    """`_wa_conversa_simples` usa a mesma busca — senão o "Agora não" abre thread
    paralela de quem já estava conversando."""
    with pool.connection() as c:
        lead = c.execute(
            """insert into prospeccao (conta_id, empresa, whatsapp) values (%s,'Doce Mell',%s)
               returning id""", (CONTA, "+" + NUM13)).fetchone()[0]
        conv = c.execute(
            """insert into conversas (conta_id, contato_ref) values (%s,%s) returning id""",
            (CONTA, NUM12)).fetchone()[0]
        assert pp._wa_conversa_simples(c, CONTA, lead, NUM13, "agora não", "b1") == conv
        c.commit()
        assert _conversas(c) == 1
        # e a órfã encontrada passa a ser a conversa do lead
        assert c.execute("select prospeccao_id from conversas where id=%s",
                         (conv,)).fetchone()[0] == lead
