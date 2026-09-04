"""A medição diária de falhas de decifragem do WhatsApp.

O que precisa estar certo, porque é daqui que vai sair a resposta pra "isso
piorou?":

* o retry não pode inflar o número — 110 linhas de log com 19 ids são 19 mensagens;
* grupo, canal e status ficam de fora: o serviço os descarta de propósito, então
  falha ali não é perda de nada;
* cliente (`fromMe=false`) e eco da empresa (`fromMe=true`) contam separados — são
  problemas diferentes, com gravidade diferente;
* a correlação só olha dia FECHADO, e distingue "não chegou" de "não deu pra saber".
"""
import os
from datetime import datetime, timedelta, timezone

import pytest
from psycopg_pool import ConnectionPool

from finance import wa_decifra

_SQL = """
create table wa_qr_log (id bigserial primary key, conta_id bigint, nivel text default 'warn',
  msg text not null default '', dados jsonb, criado_em timestamptz not null default now());
create table mensagens (id bigserial primary key, conversa_id bigint, provider_sid text,
  criado_em timestamptz default now());
create table wa_decifra_diario (
  dia date not null, conta_id bigint not null, from_me boolean not null,
  ocorrencias int not null default 0, ids_distintos int not null default 0,
  chegaram int, nunca_chegaram int, correlacionado_em timestamptz,
  apurado_em timestamptz not null default now(),
  primary key (dia, conta_id, from_me));
"""

CONTA = 34


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_wa_decifra_test"
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


def _falha(c, *, msg_id, from_me, quando, jid="5586999990000@s.whatsapp.net", conta=CONTA):
    c.execute("""insert into wa_qr_log (conta_id, msg, dados, criado_em)
                 values (%s, 'failed to decrypt message',
                         jsonb_build_object('key', jsonb_build_object(
                           'id', %s::text, 'fromMe', %s::boolean, 'remoteJid', %s::text)),
                         %s)""",
              (conta, msg_id, from_me, jid, quando))


def _agora(horas=0):
    return datetime.now(timezone.utc) - timedelta(hours=horas)


def _linhas(c):
    return c.execute("""select dia, conta_id, from_me, ocorrencias, ids_distintos,
                               chegaram, nunca_chegaram
                          from wa_decifra_diario order by dia, from_me""").fetchall()


# ------------------------------------------------------------------ a contagem

def test_o_retry_nao_infla_a_contagem(pool):
    """110 linhas com 19 ids são 19 mensagens — foi o que a medição de 04/09 mostrou."""
    with pool.connection() as c:
        for _ in range(6):                       # o mesmo id, seis entregas
            _falha(c, msg_id="AAA", from_me=True, quando=_agora(2))
        _falha(c, msg_id="BBB", from_me=True, quando=_agora(2))
        c.commit()
        wa_decifra._apurar(c)
        c.commit()
        (_dia, _ct, from_me, ocorrencias, distintos, _ch, _nc) = _linhas(c)[0]
    assert (from_me, ocorrencias, distintos) == (True, 7, 2)


def test_cliente_e_eco_contam_separados(pool):
    with pool.connection() as c:
        _falha(c, msg_id="CLI", from_me=False, quando=_agora(2))
        _falha(c, msg_id="ECO1", from_me=True, quando=_agora(2))
        _falha(c, msg_id="ECO2", from_me=True, quando=_agora(2))
        c.commit()
        wa_decifra._apurar(c)
        c.commit()
        linhas = _linhas(c)
    assert [(l[2], l[4]) for l in linhas] == [(False, 1), (True, 2)]


def test_grupo_canal_e_status_ficam_de_fora(pool):
    """O serviço descarta os três de propósito: falha ali não perdeu nada."""
    with pool.connection() as c:
        _falha(c, msg_id="G", from_me=False, quando=_agora(2), jid="12036@g.us")
        _falha(c, msg_id="S", from_me=False, quando=_agora(2), jid="status@broadcast")
        _falha(c, msg_id="N", from_me=False, quando=_agora(2), jid="0029Va@newsletter")
        _falha(c, msg_id="OK", from_me=False, quando=_agora(2))
        c.commit()
        wa_decifra._apurar(c)
        c.commit()
        linhas = _linhas(c)
    assert len(linhas) == 1 and linhas[0][4] == 1        # só a conversa 1:1


def test_linha_sem_id_ou_sem_direcao_nao_entra(pool):
    """Sem id não dá pra deduplicar o retry; sem direção não dá pra dizer se é
    cliente ou eco. Entrar como zero seria pior que não entrar."""
    with pool.connection() as c:
        c.execute("""insert into wa_qr_log (conta_id, msg, dados, criado_em)
                     values (%s,'failed to decrypt message',
                             '{"key": {"fromMe": true}}'::jsonb, now())""", (CONTA,))
        c.execute("""insert into wa_qr_log (conta_id, msg, dados, criado_em)
                     values (%s,'failed to decrypt message',
                             '{"key": {"id": "X"}}'::jsonb, now())""", (CONTA,))
        c.commit()
        wa_decifra._apurar(c)
        c.commit()
        assert _linhas(c) == []


def test_reapurar_atualiza_em_vez_de_duplicar(pool):
    """Roda a cada 2 min: tem que ser idempotente e absorver o que chegou depois."""
    with pool.connection() as c:
        _falha(c, msg_id="A", from_me=True, quando=_agora(1))
        c.commit()
        wa_decifra._apurar(c); c.commit()
        _falha(c, msg_id="B", from_me=True, quando=_agora(1))
        c.commit()
        wa_decifra._apurar(c); c.commit()
        linhas = _linhas(c)
    assert len(linhas) == 1 and linhas[0][4] == 2


def test_fora_da_janela_de_48h_nao_entra(pool):
    with pool.connection() as c:
        _falha(c, msg_id="VELHO", from_me=True, quando=_agora(72))
        c.commit()
        wa_decifra._apurar(c); c.commit()
        assert _linhas(c) == []


# ------------------------------------------------------------------ a correlação

def test_correlacao_separa_o_que_chegou_do_que_se_perdeu(pool):
    with pool.connection() as c:
        ontem = _agora(26)
        _falha(c, msg_id="CHEGOU", from_me=True, quando=ontem)
        _falha(c, msg_id="PERDIDA", from_me=True, quando=ontem)
        # o retry entregou a primeira; a segunda nunca virou mensagem
        c.execute("insert into mensagens (conversa_id, provider_sid) values (1,'CHEGOU')")
        c.commit()
        wa_decifra._apurar(c); c.commit()
        assert wa_decifra._correlacionar(c) == 1
        c.commit()
        (_d, _ct, _fm, _oc, distintos, chegaram, nunca) = _linhas(c)[0]
    assert (distintos, chegaram, nunca) == (2, 1, 1)


def test_o_dia_de_hoje_nao_e_correlacionado(pool):
    """Ainda pode chegar. Fechar hoje contaria como perda o que só está atrasado."""
    with pool.connection() as c:
        _falha(c, msg_id="HOJE", from_me=False, quando=_agora(1))
        c.commit()
        wa_decifra._apurar(c); c.commit()
        assert wa_decifra._correlacionar(c) == 0
        c.commit()
        assert _linhas(c)[0][5] is None            # chegaram segue nulo


def test_correlacao_roda_uma_vez_so(pool):
    with pool.connection() as c:
        _falha(c, msg_id="A", from_me=True, quando=_agora(26))
        c.commit()
        wa_decifra._apurar(c); c.commit()
        assert wa_decifra._correlacionar(c) == 1
        c.commit()
        assert wa_decifra._correlacionar(c) == 0    # já apurado, não repete


def test_sem_log_a_linha_fecha_sem_inventar_numero(pool):
    """Se o log já rolou pra fora das 48h, não dá pra saber o que chegou — e dizer
    'nunca chegou' seria mentira."""
    with pool.connection() as c:
        ontem = (_agora(26).astimezone(timezone(timedelta(hours=-3)))).date()
        c.execute("""insert into wa_decifra_diario (dia, conta_id, from_me, ocorrencias, ids_distintos)
                     values (%s,%s,true,5,3)""", (ontem, CONTA))
        c.commit()
        assert wa_decifra._correlacionar(c) == 1
        c.commit()
        row = c.execute("""select chegaram, nunca_chegaram, correlacionado_em
                             from wa_decifra_diario""").fetchone()
    assert row[0] is None and row[1] is None and row[2] is not None


# ------------------------------------------------------------------ a rotina

def test_rodar_e_o_resumo(pool):
    with pool.connection() as c:
        _falha(c, msg_id="A", from_me=False, quando=_agora(26))
        _falha(c, msg_id="B", from_me=True, quando=_agora(2))
        c.execute("insert into mensagens (conversa_id, provider_sid) values (1,'A')")
        c.commit()
    r = wa_decifra.rodar(pool)
    assert r["apuradas"] == 2 and r["correlacionadas"] == 1
    linhas = wa_decifra.resumo(pool)
    assert len(linhas) == 2
    ontem = [l for l in linhas if l["from_me"] is False][0]
    assert (ontem["ids_distintos"], ontem["chegaram"], ontem["nunca_chegaram"]) == (1, 1, 0)
