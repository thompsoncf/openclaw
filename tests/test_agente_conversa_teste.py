"""Ligar o agente numa conversa só — o caminho de teste, que não funcionava.

O painel oferece um botão de agente POR CONVERSA, e é assim que se experimenta o
agente antes de soltá-lo na caixa inteira: liga numa conversa, acompanha as
respostas, depois liga pra todo mundo. Na prática o botão não fazia nada com o
agente-mestre desligado, e o dono do produto viu o sintoma exato:

    "ativo a IA no chat de uma conversa, mando a mensagem e ele desativa"

Eram três travas, todas presas no mesmo interruptor:

1. **A gravação da mensagem** fazia `agente_ativo = <mestre>`. Com o mestre
   desligado, toda mensagem que chegava zerava a conversa — a chave voltava
   sozinha no primeiro "oi" do cliente. É o que ele via.
2. **O webhook** só chamava o agente `if agente_on` (o mestre), então a conversa
   ligada nunca era atendida.
3. **O próprio agente** retornava logo no começo quando o mestre estava desligado.

Agora: o mestre LIGA a conversa e nunca a desliga; o webhook acorda o agente
quando o mestre OU a conversa estão ligados; e o agente atende a conversa ligada
à mão mesmo com o mestre desligado.

Schema mínimo dos caminhos exercitados.
"""
import os

import pytest
from psycopg_pool import ConnectionPool

from web import painel_prospeccao as pp

CONTA = 41
NUM = "5586998392961"

_SQL = """
create table prospeccao (id bigserial primary key, conta_id bigint, vendedor_id bigint,
  empresa text, contato text, tipo text default 'pj',
  telefone text, whatsapp text, email text, origem text,
  status text default 'novo', temperatura text default 'frio', estagio text default 'base',
  atualizado_em timestamptz default now(), criado_em timestamptz default now());
create table conversas (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  canal text default 'whatsapp', contato_ref text, contato_nome text,
  status text default 'aberta', agente_ativo boolean default false,
  janela_expira_em timestamptz,
  ultima_msg_em timestamptz default now(), criado_em timestamptz default now(), chip_id bigint, visto_ate_id bigint);
create table mensagens (id bigserial primary key, conversa_id bigint, canal text,
  direcao text, autor text, texto text, membro_id bigint, provider_sid text,
  status text, criado_em timestamptz default now(),
  midia_ref jsonb, midia_tipo text, midia_meta jsonb);
create unique index if not exists idx_mensagens_sid_conversa
  on mensagens (conversa_id, provider_sid) where provider_sid is not null;
create table wa_contatos (conta_id bigint, numero8 text, nome text,
  da_agenda boolean default false, primary key (conta_id, numero8));
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text, ativo boolean default true, cockpit_pausado boolean default false);
"""


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_agente_teste"
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


def _recebe(c, texto, *, sid, mestre):
    """Uma mensagem chegando, com o agente-mestre ligado ou desligado."""
    return pp._wa_inbound_conversa(c, CONTA, NUM, texto, sid, "Cliente", mestre)[0]


def _recebe_par(c, texto, *, sid, mestre=False):
    """(conversa, nova) — pra quem está testando a reentrega."""
    return pp._wa_inbound_conversa(c, CONTA, NUM, texto, sid, "Cliente", mestre)


def _ligada(c, conv_id):
    return c.execute("select agente_ativo from conversas where id=%s", (conv_id,)).fetchone()[0]


# ------------------------------------------- o sintoma: a chave voltava sozinha

def test_mensagem_nao_desliga_conversa_ligada_a_mao(pool):
    """O caso do chamado. Com o mestre desligado, o cliente responde e a conversa
    que o dono acabou de ligar continua ligada."""
    with pool.connection() as c:
        conv = _recebe(c, "oi", sid="m1", mestre=False)
        c.execute("update conversas set agente_ativo=true where id=%s", (conv,))   # o botão do painel
        _recebe(c, "e aí, quanto custa?", sid="m2", mestre=False)
        c.commit()
        assert _ligada(c, conv) is True


def test_mestre_ligado_continua_reativando(pool):
    """O comportamento que já existia e não pode se perder: com o mestre ligado, uma
    conversa desligada volta a ser atendida quando o cliente escreve de novo."""
    with pool.connection() as c:
        conv = _recebe(c, "oi", sid="m1", mestre=False)
        c.execute("update conversas set agente_ativo=false where id=%s", (conv,))
        _recebe(c, "voltei", sid="m2", mestre=True)
        c.commit()
        assert _ligada(c, conv) is True


def test_humano_que_assumiu_continua_intocado(pool):
    """`status='pendente'` é o humano dizendo "essa conversa é minha". Nem o mestre
    ligado toma ela de volta."""
    with pool.connection() as c:
        conv = _recebe(c, "oi", sid="m1", mestre=False)
        c.execute("update conversas set status='pendente', agente_ativo=false where id=%s", (conv,))
        _recebe(c, "alô?", sid="m2", mestre=True)
        c.commit()
        assert _ligada(c, conv) is False
        assert c.execute("select status from conversas where id=%s", (conv,)).fetchone()[0] == "pendente"


def test_conversa_desligada_segue_desligada(pool):
    """Sem o mestre e sem o botão, mensagem nenhuma liga o agente sozinha."""
    with pool.connection() as c:
        conv = _recebe(c, "oi", sid="m1", mestre=False)
        _recebe(c, "de novo", sid="m2", mestre=False)
        c.commit()
        assert _ligada(c, conv) is False


# --------------------------------- a porta que faltava: quem o webhook vai acordar

def test_webhook_acorda_o_agente_na_conversa_ligada(pool):
    """Mestre desligado + conversa ligada = atende. É o modo teste."""
    with pool.connection() as c:
        conv = _recebe(c, "oi", sid="m1", mestre=False)
        c.execute("update conversas set agente_ativo=true where id=%s", (conv,))
        assert pp._agente_atende(c, conv, False) is True


def test_webhook_nao_acorda_conversa_desligada(pool):
    with pool.connection() as c:
        conv = _recebe(c, "oi", sid="m1", mestre=False)
        assert pp._agente_atende(c, conv, False) is False


def test_mestre_ligado_acorda_sempre(pool):
    """Com o mestre ligado nem precisa consultar a conversa — a caixa inteira é dele."""
    with pool.connection() as c:
        conv = _recebe(c, "oi", sid="m1", mestre=True)
        assert pp._agente_atende(c, conv, True) is True


# -------------------------------------- a reentrega: uma resposta por mensagem
#
# O wa-qr reentrega a MESMA mensagem quando a conexão oscila (`messages.upsert` type
# 'append'). Em 15/08 um único "?" do cliente foi entregue TRÊS vezes ao webhook: a
# mensagem não duplicou no banco — o índice (conversa_id, provider_sid) barrou —, mas
# o `on conflict do nothing` era SILENCIOSO e quem chamava seguia como se fosse
# mensagem nova. O agente rodou três vezes e o cliente recebeu três respostas
# diferentes, uma pedindo desculpa pela confusão da outra:
#
#     22:10:37  cliente: "?"
#     22:10:39  agente: "Me perdi um pouco aqui..."
#     22:10:44  agente: "Me perdi aqui..."
#     22:10:45  agente: "Desculpa a confusão!..."

def test_mensagem_nova_diz_que_e_nova(pool):
    with pool.connection() as c:
        _conv, nova = _recebe_par(c, "oi", sid="m1")
        c.commit()
        assert nova is True


def test_a_mesma_mensagem_de_novo_nao_e_nova(pool):
    """O sid é o mesmo — é a reentrega, não uma segunda mensagem do cliente."""
    with pool.connection() as c:
        conv1, _ = _recebe_par(c, "?", sid="m1")
        conv2, nova = _recebe_par(c, "?", sid="m1")
        c.commit()
        assert nova is False
        assert conv1 == conv2                       # e continua a mesma conversa
        n = c.execute("select count(*) from mensagens where conversa_id=%s", (conv1,)).fetchone()[0]
        assert n == 1                               # nada duplicou


def test_tres_entregas_uma_resposta(pool):
    """O caso do chamado: três entregas do mesmo "?" e só a primeira acorda o agente."""
    with pool.connection() as c:
        acordou = [nova for _ in range(3)
                   if (nova := _recebe_par(c, "?", sid="mesmo-id")[1]) or True]
        c.commit()
        assert acordou == [True, False, False]


def test_texto_diferente_com_o_mesmo_sid_continua_sendo_repeticao(pool):
    """Quem manda o texto é o WhatsApp junto com o id; se o id repete, é a mesma
    mensagem. Confiar no texto abriria a porta pra reentrega com acento diferente."""
    with pool.connection() as c:
        _recebe_par(c, "oi", sid="m1")
        _conv, nova = _recebe_par(c, "oi ", sid="m1")
        c.commit()
        assert nova is False


def test_sem_sid_toda_entrega_e_nova(pool):
    """Sem id do provedor não há como saber que é repetição — e perder mensagem de
    cliente é pior que responder duas vezes. O índice só cobre provider_sid não nulo."""
    with pool.connection() as c:
        _recebe_par(c, "oi", sid=None)
        _conv, nova = _recebe_par(c, "oi", sid=None)
        c.commit()
        assert nova is True


def test_duas_mensagens_de_verdade_acordam_as_duas(pool):
    """A trava não pode calar o cliente que escreve duas vezes seguidas."""
    with pool.connection() as c:
        assert _recebe_par(c, "oi", sid="m1")[1] is True
        assert _recebe_par(c, "tudo bem?", sid="m2")[1] is True
        c.commit()


# ------------------------------------------------- o horário no modo teste

def test_modo_teste_fala_a_qualquer_hora():
    """O teste quase sempre é fora do expediente — fim de semana, à noite. Um agente
    mudo nessa hora se parece com um agente quebrado, que foi o que aconteceu."""
    from finance import agente as ag
    assert ag._pode_falar_agora({"ativo": False, "horario": "comercial"}) is True
    assert ag._pode_falar_agora({"ativo": False, "horario": "24h"}) is True


def test_com_mestre_ligado_o_horario_da_empresa_manda():
    """Aí não é teste, é atendimento: quem decide quando falar é a conta."""
    from finance import agente as ag
    assert ag._pode_falar_agora({"ativo": True, "horario": "24h"}) is True
    # 'comercial' depende do relógio — o que se afirma é que a decisão é DELE,
    # não que hoje seja dia útil às 10h
    cfg = {"ativo": True, "horario": "comercial"}
    assert ag._pode_falar_agora(cfg) == ag._horario_ok(cfg)
