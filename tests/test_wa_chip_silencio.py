"""A faixa do chip tem que discordar de si mesma quando a caixa parou.

O caso que ensinou: o serviço dizia CONECTADO (é o que ficou na memória do último
'open' do socket) e a caixa não recebia nada havia três horas. As duas informações
existiam, em telas diferentes — "conectado" na faixa de cima, "📥 Última recebida:
15/08 13:28" na aba Canais — e ninguém junta 13:28 com "agora são 16:30" olhando de
relance. Quem percebeu foi o dono do produto, no olho.

Agora a conta é feita e escrita: "conectado · sem receber há 3h". O estado da SESSÃO
continua vindo do serviço; o silêncio vem das mensagens, que é o que o cliente
realmente sente.

O limiar é uma hora — abaixo disso, silêncio é rotina (almoço, cliente sem assunto)
e avisar seria o alarme que a gente aprende a ignorar.
"""
import os
from datetime import datetime, timedelta, timezone

import pytest
from psycopg_pool import ConnectionPool

from web import painel_prospeccao as pp

CONTA = 13

_SQL = """
create table conversas (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  canal text default 'whatsapp', contato_ref text, contato_nome text,
  status text default 'aberta', ultima_msg_em timestamptz default now(), chip_id bigint);
create table mensagens (id bigserial primary key, conversa_id bigint, canal text,
  direcao text, autor text, texto text, provider_sid text,
  criado_em timestamptz default now(),
  midia_ref jsonb, midia_tipo text, midia_meta jsonb);

-- unicidade por CONVERSA, não global: o id do WhatsApp é o mesmo nas duas pontas
-- da mensagem, e global fazia a conta que recebe perder a dela (migração 159)
create unique index if not exists idx_mensagens_sid_conversa
  on mensagens (conversa_id, provider_sid) where provider_sid is not null;
"""


@pytest.fixture()
def pool(monkeypatch):
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_chip_silencio_test"
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
    monkeypatch.setattr(pp, "get_pool", lambda: p)
    yield p
    p.close()


def _recebida(c, *, ha_minutos, canal="whatsapp", direcao="in"):
    conv = c.execute("insert into conversas (conta_id, canal) values (%s,%s) returning id",
                     (CONTA, canal)).fetchone()[0]
    c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, texto, criado_em)
                 values (%s,%s,%s,'lead','oi',%s)""",
              (conv, canal, direcao,
               datetime.now(timezone.utc) - timedelta(minutes=ha_minutos)))
    c.commit()


# ------------------------------------------------------------- o texto do aviso

def test_silencio_curto_nao_vira_aviso():
    """Uma hora é o piso. Alarme que dispara à toa é alarme que ninguém lê."""
    assert pp._ha_quanto(0) == ""
    assert pp._ha_quanto(59) == ""


def test_silencio_em_minutos_horas_e_dias():
    assert pp._ha_quanto(60) == "há 60min"
    assert pp._ha_quanto(119) == "há 119min"
    assert pp._ha_quanto(180) == "há 3h"
    assert pp._ha_quanto(3 * 24 * 60) == "há 3 dias"


def test_conta_que_nunca_recebeu_nao_e_silencio_suspeito():
    """Conta recém-conectada não pode nascer com aviso amarelo na cara."""
    assert pp._ha_quanto(None) == ""


# ------------------------------------------------------------------ a medição

def test_mede_a_ultima_recebida(pool):
    with pool.connection() as c:
        _recebida(c, ha_minutos=185)
    assert 180 <= pp._wa_minutos_sem_receber(CONTA) <= 190


def test_mensagem_enviada_nao_conta_como_sinal_de_vida(pool):
    """O que prova que a sessão está entregando é o que ENTRA. Disparo de campanha
    sai pelo painel mesmo com o socket mudo — contar a saída esconderia o defeito."""
    with pool.connection() as c:
        _recebida(c, ha_minutos=200, direcao="in")
        _recebida(c, ha_minutos=2, direcao="out")
    assert pp._wa_minutos_sem_receber(CONTA) >= 195


def test_outro_canal_nao_disfarca_o_whatsapp_parado(pool):
    """E-mail chegando não diz nada sobre o chip."""
    with pool.connection() as c:
        _recebida(c, ha_minutos=200, canal="whatsapp")
        _recebida(c, ha_minutos=1, canal="email")
    assert pp._wa_minutos_sem_receber(CONTA) >= 195


def test_conta_vizinha_nao_conta(pool):
    """Multi-tenant: o movimento de outra empresa não pode calar o aviso desta."""
    with pool.connection() as c:
        _recebida(c, ha_minutos=200)
        conv = c.execute("insert into conversas (conta_id) values (99) returning id").fetchone()[0]
        c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, texto)
                     values (%s,'whatsapp','in','lead','oi')""", (conv,))
        c.commit()
    assert pp._wa_minutos_sem_receber(CONTA) >= 195


def test_conta_sem_mensagem_nenhuma(pool):
    assert pp._wa_minutos_sem_receber(CONTA) is None


def test_o_caso_da_doce_mell(pool):
    """Ponta a ponta do que a tela vai dizer: conectada, e sem receber há 3h."""
    with pool.connection() as c:
        _recebida(c, ha_minutos=182)
    assert pp._ha_quanto(pp._wa_minutos_sem_receber(CONTA)) == "há 3h"
