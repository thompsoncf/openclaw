"""Alvo que morre em `erro` sem dizer por quê.

`wa_status='erro'` tira o lead da fila pra sempre (a fila é `wa_status is null`).
Então o que estiver gravado em `wa_erro_codigo`/`wa_erro_msg` na hora é a última —
e única — coisa que a tela vai dizer sobre ele. É por ali que o dono decide se vale
apertar "recolocar na fila" e gastar outra mensagem de marketing.

Em produção, 10 dos 57 alvos em erro de uma conta estavam com os DOIS campos nulos.
Dois caminhos levavam nisso:

1. **O callback sem código apagava o código.** O Twilio manda 'failed'/'undelivered'
   sem `ErrorCode` de vez em quando. `falha_na_entrega` gravava esse vazio por cima
   do 63024 que já estava lá, trocando um motivo conhecido por um erro pelado.

2. **A fila esgotada não escrevia motivo nenhum.** Quando acabavam os números, o
   motor marcava `erro` só com o status — sem uma palavra sobre o que aconteceu.
"""
import os

import pytest
from psycopg_pool import ConnectionPool

from finance import campanhas_motor as cm

_BASE_SQL = """
create table prospeccao (id bigserial primary key, conta_id bigint, empresa text,
  cnpj text, whatsapp text, telefone text, decisor_telefones jsonb, estagio text);
create table campanha_alvos (id bigserial primary key, campanha_id bigint, prospeccao_id bigint,
  status text default 'fila', wa_status text, wa_em timestamptz, wa_sid text,
  wa_erro_codigo text, wa_erro_msg text, wa_numero text, alvo_telefone text,
  wa_tentados jsonb not null default '[]'::jsonb, wa_tentativas int not null default 0);
"""


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_campanha_erro_mudo_test"
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


def _alvo(pool, *, numero="86988725218", tentados=None, tentativas=0,
          codigo=None, msg=None, alvo_telefone=None):
    """Um alvo com UM número só — assim a fila acaba na primeira falha."""
    with pool.connection() as c:
        pid = c.execute("""insert into prospeccao (conta_id, empresa, whatsapp, estagio)
                            values (1,'Alvo',%s,'prospecto') returning id""",
                        (numero,)).fetchone()[0]
        aid = c.execute(
            """insert into campanha_alvos (campanha_id, prospeccao_id, wa_sid, wa_numero,
                   wa_erro_codigo, wa_erro_msg, wa_tentados, wa_tentativas, alvo_telefone)
               values (1,%s,'SM1',%s,%s,%s,%s::jsonb,%s,%s) returning id""",
            (pid, numero, codigo, msg, tentados or "[]", tentativas,
             alvo_telefone)).fetchone()[0]
        c.commit()
    return aid


def _ler(pool, aid):
    with pool.connection() as c:
        return c.execute("""select wa_status, wa_erro_codigo, wa_erro_msg, wa_tentativas
                              from campanha_alvos where id=%s""", (aid,)).fetchone()


# ------------------------------------- 1 · callback sem código não apaga o motivo

def test_callback_sem_codigo_preserva_o_63024_que_ja_estava_la(pool):
    """O caso que aconteceu: 63024 chega, e depois vem um 'failed' pelado. O motivo
    conhecido tem que sobreviver — é ele que explica o alvo pro dono."""
    aid = _alvo(pool, codigo="63024", msg="número não tem WhatsApp")
    with pool.connection() as c:
        parou = cm.falha_na_entrega(c, aid, "", "")
        c.commit()
    status, codigo, msg, _ = _ler(pool, aid)
    assert parou and status == "erro"
    assert codigo == "63024" and msg == "número não tem WhatsApp"


def test_callback_com_codigo_novo_ainda_atualiza(pool):
    """Contraprova: preservar não é congelar. Código novo entra por cima."""
    aid = _alvo(pool, codigo="63024", msg="antigo")
    with pool.connection() as c:
        cm.falha_na_entrega(c, aid, "63016", "fora da janela")
        c.commit()
    _, codigo, msg, _ = _ler(pool, aid)
    assert codigo == "63016" and msg == "fora da janela"


def test_alvo_que_nunca_teve_codigo_nao_fica_sem_nada(pool):
    """Sem motivo anterior E sem motivo novo, ainda sobra o da fila esgotada."""
    aid = _alvo(pool, alvo_telefone="(86) 98872-5218")
    with pool.connection() as c:
        parou = cm.falha_na_entrega(c, aid, "", "")
        c.commit()
    status, _, _, _ = _ler(pool, aid)
    assert parou and status == "erro"
    # o alvo parou aqui; quem escreve o motivo neste caminho é _wa_esgotou_a_fila,
    # testado abaixo — o que importa é que ele NÃO saiu da fila em silêncio
    cm._wa_esgotou_a_fila(pool, aid)
    assert _ler(pool, aid)[2]


# --------------------------------------------- 2 · fila esgotada escreve o motivo

def test_fila_esgotada_escreve_o_motivo(pool):
    aid = _alvo(pool, tentativas=1, tentados='["86988725218"]')
    cm._wa_esgotou_a_fila(pool, aid)
    status, _, msg, tentativas = _ler(pool, aid)
    assert status == "erro"
    assert "já foram tentados" in msg
    # não inventa tentativa: quem contou foi quem tentou
    assert tentativas == 1


def test_fila_esgotada_nao_apaga_o_motivo_de_verdade(pool):
    """O 63024 do último número é mais informativo que a frase genérica."""
    aid = _alvo(pool, tentativas=1, codigo="63024", msg="número não tem WhatsApp")
    cm._wa_esgotou_a_fila(pool, aid)
    _, codigo, msg, _ = _ler(pool, aid)
    assert codigo == "63024" and msg == "número não tem WhatsApp"
