"""O que faz uma mensagem recebida virar lead — e o que não faz.

Duas regras nasceram do mesmo chamado, e são opostas:

1. **Resposta de campanha (Twilio/Cloud).** As empresas prospectadas respondem com
   bot ("no momento não estamos disponíveis") e isso virava lead QUENTE, sujando o
   funil e o placar do vendedor. Agora a primeira mensagem não reconhecível de quem
   ainda está na BASE não promove: promove na segunda, ou depois que a empresa
   responder. Bot manda o automático e cala; pessoa continua.

2. **Contato do histórico (QR).** A importação do WhatsApp traz quem já falava com a
   empresa. Antes, esse contato mandando mensagem era um beco sem saída: anexava na
   conversa órfã e nunca entrava no funil — numa padaria, o cliente pedindo bolo
   ficava invisível pra fila. Agora vira lead e cai na distribuição.

Schema mínimo dos caminhos exercitados; nada de migração.
"""
import os
from types import SimpleNamespace

import pytest
from psycopg_pool import ConnectionPool

from web import painel_prospeccao as pp

CONTA = 5
NUM = "558698392961"

_SQL = """
create table prospeccao (id bigserial primary key, conta_id bigint, vendedor_id bigint,
  empresa text, contato text, cnpj text, cpf text, tipo text default 'pj',
  telefone text, whatsapp text, email text, origem text,
  status text default 'novo', temperatura text default 'frio', estagio text default 'base',
  atualizado_em timestamptz default now(), criado_em timestamptz default now());
create table conversas (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  canal text default 'whatsapp', contato_ref text, contato_nome text,
  status text default 'aberta', agente_ativo boolean default true,
  responsavel_membro_id bigint, janela_expira_em timestamptz,
  ultima_msg_em timestamptz default now(), criado_em timestamptz default now());
create table mensagens (id bigserial primary key, conversa_id bigint, canal text,
  direcao text, autor text, texto text, membro_id bigint, provider_sid text,
  status text, criado_em timestamptz default now());
create table wa_contatos (conta_id bigint, numero8 text, nome text,
  da_agenda boolean default false, primary key (conta_id, numero8));
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text, ativo boolean default true, cockpit_pausado boolean default false);

-- unicidade por CONVERSA, não global: o id do WhatsApp é o mesmo nas duas pontas
-- da mensagem, e global fazia a conta que recebe perder a dela (migração 159)
create unique index if not exists idx_mensagens_sid_conversa
  on mensagens (conversa_id, provider_sid) where provider_sid is not null;
"""


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_inbound_lead_test"
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


def _alvo_de_campanha(c, *, empresa="Clínica Bem Viver"):
    """Um contato da BASE, como a campanha cria: ainda não é lead do funil."""
    return c.execute(
        """insert into prospeccao (conta_id, empresa, whatsapp, estagio, temperatura, origem)
           values (%s,%s,%s,'base','frio','google_places') returning id""",
        (CONTA, empresa, "+" + NUM)).fetchone()[0]


def _estado(c, lead_id):
    return c.execute("select estagio, temperatura from prospeccao where id=%s",
                     (lead_id,)).fetchone()


def _recebe(c, texto, *, sid, continuidade):
    # devolve (conversa, nova); estes testes só olham a conversa
    return pp._wa_inbound_conversa(c, CONTA, NUM, texto, sid, "Perfil", False,
                                   exigir_continuidade=continuidade)[0]


# ------------------------------------------------------ 1. resposta de campanha

def test_bot_respondendo_campanha_nao_vira_lead_quente(pool):
    """O caso real: a empresa prospectada tem atendente automático."""
    with pool.connection() as c:
        lead = _alvo_de_campanha(c)
        _recebe(c, "O Dom agradece seu contato, no momento não estamos disponíveis.",
                sid="s1", continuidade=True)
        c.commit()
        assert _estado(c, lead) == ("base", "frio")


def test_pessoa_que_insiste_vira_lead_na_segunda(pool):
    """Bot manda o automático e cala; pessoa continua. É a diferença que a trava usa."""
    with pool.connection() as c:
        lead = _alvo_de_campanha(c)
        _recebe(c, "quem é?", sid="s1", continuidade=True)
        assert _estado(c, lead) == ("base", "frio")          # ainda não
        _recebe(c, "ah sim, quanto custa?", sid="s2", continuidade=True)
        c.commit()
        assert _estado(c, lead) == ("lead", "quente")        # agora sim


def test_resposta_depois_da_empresa_falar_tambem_conta(pool):
    """Ela respondeu, a empresa retornou, ela voltou — é conversa de verdade."""
    with pool.connection() as c:
        lead = _alvo_de_campanha(c)
        conv = _recebe(c, "quem é?", sid="s1", continuidade=True)
        c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, texto)
                     values (%s,'whatsapp','out','humano','Somos da Zaq, tudo bem?')""", (conv,))
        _recebe(c, "entendi", sid="s2", continuidade=True)
        c.commit()
        assert _estado(c, lead) == ("lead", "quente")


def test_no_qr_a_campanha_segue_como_sempre(pool):
    """No QR não existe template com botão — o disparo é texto solto, e a regra de lá
    não muda. Sem a trava, a primeira resposta promove como antes."""
    with pool.connection() as c:
        lead = _alvo_de_campanha(c)
        _recebe(c, "qualquer coisa", sid="s1", continuidade=False)
        c.commit()
        assert _estado(c, lead) == ("lead", "quente")


def test_quem_chegou_sozinho_esquenta_na_primeira(pool):
    """A trava é pra quem NÓS fomos atrás. Quem procurou a empresa (origem inbound) é
    cliente falando com a gente: esquenta na hora, senão a caixa pararia de reagir a
    quem já está sendo atendido."""
    with pool.connection() as c:
        lead = c.execute(
            """insert into prospeccao (conta_id, empresa, whatsapp, estagio, temperatura, origem)
               values (%s,'Chegou sozinho',%s,'lead','frio','whatsapp_inbound') returning id""",
            (CONTA, "+" + NUM)).fetchone()[0]
        _recebe(c, "oi", sid="s1", continuidade=True)
        c.commit()
        assert _estado(c, lead) == ("lead", "quente")


def test_alvo_de_campanha_ja_promovido_tambem_pega_a_trava(pool):
    """O buraco que a primeira versão tinha: a trava olhava `estagio='base'`, mas a base
    é esvaziada em lote pelo botão "Promover" ANTES da campanha rodar. Quando o bot
    respondia, o alvo já era 'lead' e a trava não pegava nada — no banco de produção não
    existia UMA linha 'base'. O que decide é a origem, não o estágio."""
    with pool.connection() as c:
        lead = c.execute(
            """insert into prospeccao (conta_id, empresa, whatsapp, estagio, temperatura, origem)
               values (%s,'Promovido antes da campanha',%s,'lead','frio','google_places')
               returning id""",
            (CONTA, "+" + NUM)).fetchone()[0]
        _recebe(c, "no momento não estamos disponíveis", sid="s1", continuidade=True)
        c.commit()
        assert _estado(c, lead) == ("lead", "frio"), "bot não pode esquentar"
        _recebe(c, "quanto custa?", sid="s2", continuidade=True)
        c.commit()
        assert _estado(c, lead) == ("lead", "quente"), "pessoa continuando, sim"


# ------------------------------------------------------ 2. contato do histórico

def _conversa_importada(c, nome="Mariêh Louise", msgs=3):
    """Conversa órfã, como a importação do histórico por QR cria: sem lead."""
    conv = c.execute(
        """insert into conversas (conta_id, prospeccao_id, canal, contato_ref, contato_nome)
           values (%s, null, 'whatsapp', %s, %s) returning id""",
        (CONTA, NUM, nome)).fetchone()[0]
    for i in range(msgs):
        c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, texto)
                     values (%s,'whatsapp','in','lead',%s)""", (conv, f"msg antiga {i}"))
    return conv


def test_contato_do_historico_que_fala_vira_lead(pool):
    """O caso da Mariêh: 55 mensagens desde julho, mandou 'tem fatia nuvem?' e o
    pedido ficava fora do funil, sem dono e sem ninguém avisado."""
    with pool.connection() as c:
        conv = _conversa_importada(c)
        conv_id = _recebe(c, "Boa tarde, tem fatia nuvem?", sid="s9", continuidade=False)
        c.commit()
        assert conv_id == conv, "tem que reusar a conversa que já existia"
        lead = c.execute("select prospeccao_id from conversas where id=%s",
                         (conv,)).fetchone()[0]
        assert lead is not None, "a conversa precisa passar a apontar pro lead novo"
        assert _estado(c, lead) == ("lead", "quente")


def test_o_lead_do_historico_herda_o_nome_da_conversa(pool):
    """O nome com que ela já aparecia no celular é melhor que o pushName do momento —
    senão o funil ganha 'Contato WhatsApp' no lugar de 'Mariêh Louise'."""
    with pool.connection() as c:
        _conversa_importada(c, nome="Mariêh Louise")
        _recebe(c, "oi", sid="s9", continuidade=False)
        c.commit()
        assert c.execute("select empresa from prospeccao where conta_id=%s",
                         (CONTA,)).fetchone()[0] == "Mariêh Louise"


def test_o_historico_antigo_nao_se_perde(pool):
    """Vira lead reaproveitando a conversa — as mensagens de antes continuam lá."""
    with pool.connection() as c:
        conv = _conversa_importada(c, msgs=5)
        _recebe(c, "nova", sid="s9", continuidade=False)
        c.commit()
        n = c.execute("select count(*) from mensagens where conversa_id=%s",
                      (conv,)).fetchone()[0]
        assert n == 6, "5 antigas + a que acabou de chegar"


def test_numero_novo_de_verdade_continua_virando_lead(pool):
    """Sem conversa nem base: o caminho de sempre, que já funcionava."""
    with pool.connection() as c:
        _recebe(c, "oi", sid="s9", continuidade=True)
        c.commit()
        r = c.execute("select estagio, temperatura, origem from prospeccao where conta_id=%s",
                      (CONTA,)).fetchone()
        assert r == ("lead", "quente", "whatsapp_inbound")


# ------------------------------------------------------ 3. a mensagem é sagrada

def test_rodizio_quebrado_nao_engole_a_mensagem(pool, monkeypatch):
    """O rodízio roda dentro do webhook, e falhar nele abortava a transação inteira: o
    `except` calava o erro, o commit virava ROLLBACK silencioso e a MENSAGEM RECEBIDA
    sumia — com 200 pro WhatsApp, como se tivesse dado certo. Rodízio sem dono custa um
    lead na fila; mensagem perdida custa o cliente. Agora o rodízio vai em SAVEPOINT."""
    from finance import distribuicao as dist

    def explode(*a, **kw):
        raise RuntimeError("fila travada")

    monkeypatch.setattr(dist, "atribuir_se_sem_dono", explode)
    with pool.connection() as c:
        _recebe(c, "quero fazer um pedido", sid="s9", continuidade=False)
        c.commit()
        lead = c.execute("select id from prospeccao where conta_id=%s", (CONTA,)).fetchone()
        assert lead, "o lead tem que sobreviver ao rodízio quebrado"
        assert c.execute(
            """select texto from mensagens m join conversas cv on cv.id=m.conversa_id
                where cv.prospeccao_id=%s""", (lead[0],)).fetchone()[0] \
            == "quero fazer um pedido"
