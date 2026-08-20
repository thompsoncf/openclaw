"""Callback de status atrasado não pode apagar uma falha já registrada.

O que aconteceu na campanha "CLINICA DE HARMONIZACAO FACIAL": os callbacks do
Twilio chegaram fora de ordem, em 3 segundos —

    18:26:47  enviado
    18:26:49  erro 63024      ← a falha real (o número não tem WhatsApp)
    18:26:50  enviado         ← e isto apagou a falha

A trava de "nunca rebaixar" tinha a escada `enviado=1, entregue=2, lido=3` e o
'erro' caía no `else 0`. Como `0 < 1`, o "enviado" atrasado passava por cima.

O estrago não era cosmético: sobrava o `wa_erro_codigo` órfão, o alvo NÃO voltava
pra fila de reenvio (ela procura `null` ou `erro`), o KPI de erros contava a menos
e o painel "Números não tentados" nem enxergava o lead. Foram 28 alvos em 4
campanhas segurando 156 telefones guardados na base.

A cura: 'erro' empata com 'enviado' na escada. Um "enviado" tardio não sobrescreve
mais; 'entregue'/'lido' ainda sim — aí a mensagem chegou de verdade e o sucesso é
a informação mais nova.
"""
import asyncio
import os

import pytest
from psycopg_pool import ConnectionPool

from web import painel_prospeccao as pp

_BASE_SQL = """
create table contas (id bigserial primary key, tipo text, nome text, chip_de bigint);
create table prospeccao (id bigserial primary key, conta_id bigint, empresa text,
  cnpj text, whatsapp text, telefone text, decisor_telefones jsonb);
create table campanhas (id bigserial primary key, conta_id bigint, nome text);
create table campanha_alvos (id bigserial primary key, campanha_id bigint, prospeccao_id bigint,
  wa_sid text, wa_numero text, wa_status text, wa_em timestamptz,
  wa_erro_codigo text, wa_erro_msg text, alvo_telefone text,
  wa_tentados jsonb not null default '[]'::jsonb, wa_tentativas int not null default 0);
create table campanha_eventos (id bigserial primary key, campanha_id bigint, prospeccao_id bigint,
  canal text, evento text, detalhe text, quando timestamptz default now());
create table conversas (id bigserial primary key, conta_id bigint, canal text, chip_id bigint);
create table mensagens (id bigserial primary key, conversa_id bigint, canal text,
  provider_sid text, status text, texto text);
"""


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_wa_status_ordem_test"
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


class _FakeForm(dict):
    def items(self):
        return super().items()


class _FakeRequest:
    """Só o que a rota usa: form, cabeçalhos e url."""

    class _Url:
        path = "/webhooks/twilio-status"

        def __str__(self):
            return "https://app.zaq-ia.com/webhooks/twilio-status"

    def __init__(self, params):
        self._params = params
        self.headers = {"X-Twilio-Signature": "sig", "host": "app.zaq-ia.com"}
        self.url = self._Url()

    async def form(self):
        return _FakeForm(self._params)


@pytest.fixture(autouse=True)
def ambiente(pool, monkeypatch):
    monkeypatch.setattr(pp, "get_pool", lambda: pool)
    from finance import whatsapp_twilio as wa
    monkeypatch.setattr(wa, "validar_assinatura", lambda url, params, sig: True)


def _alvo(pool, sid, status="enviado"):
    with pool.connection() as c:
        camp = c.execute("insert into campanhas (conta_id, nome) values (1,'C') returning id"
                         ).fetchone()[0]
        pid = c.execute("insert into prospeccao (conta_id, empresa) values (1,'Botoclinic') "
                        "returning id").fetchone()[0]
        aid = c.execute("""insert into campanha_alvos (campanha_id, prospeccao_id, wa_sid, wa_status)
                            values (%s,%s,%s,%s) returning id""",
                        (camp, pid, sid, status)).fetchone()[0]
        c.commit()
    return aid


def _status(pool, aid):
    with pool.connection() as c:
        return c.execute("select wa_status, wa_erro_codigo from campanha_alvos where id=%s",
                         (aid,)).fetchone()


def _callback(sid, status, codigo=""):
    p = {"MessageSid": sid, "MessageStatus": status}
    if codigo:
        p["ErrorCode"] = codigo
        p["ErrorMessage"] = "Number does not have WhatsApp"
    return asyncio.run(pp.webhook_twilio_status(_FakeRequest(p)))


def test_enviado_atrasado_nao_apaga_o_erro(pool):
    """A sequência exata da produção: enviado → failed → sent."""
    aid = _alvo(pool, "SM_ordem_1")
    _callback("SM_ordem_1", "sent")
    _callback("SM_ordem_1", "failed", codigo="63024")
    assert _status(pool, aid) == ("erro", "63024")
    _callback("SM_ordem_1", "sent")             # o callback atrasado que estragava tudo
    assert _status(pool, aid) == ("erro", "63024"), (
        "um 'enviado' tardio ressuscitava a falha como sucesso — o alvo sumia da "
        "fila de reenvio e do painel de números não tentados"
    )


def test_queued_atrasado_tambem_nao_apaga(pool):
    """'queued' e 'sending' também mapeiam pra 'enviado'."""
    aid = _alvo(pool, "SM_ordem_2")
    _callback("SM_ordem_2", "undelivered", codigo="63049")
    _callback("SM_ordem_2", "queued")
    assert _status(pool, aid)[0] == "erro"


def test_entregue_depois_do_erro_ainda_vence(pool):
    """Contraprova: se a mensagem chegou DE VERDADE, o sucesso é a informação nova
    e o erro era o relato velho. Não pode travar o alvo em 'erro' pra sempre."""
    aid = _alvo(pool, "SM_ordem_3")
    _callback("SM_ordem_3", "failed", codigo="63016")
    assert _status(pool, aid)[0] == "erro"
    _callback("SM_ordem_3", "delivered")
    assert _status(pool, aid)[0] == "entregue"
    _callback("SM_ordem_3", "read")
    assert _status(pool, aid)[0] == "lido"


def test_erro_nao_rebaixa_quem_ja_foi_entregue(pool):
    """A regra que já existia continua: erro não apaga entregue/lido."""
    aid = _alvo(pool, "SM_ordem_4", status="lido")
    _callback("SM_ordem_4", "failed", codigo="63024")
    assert _status(pool, aid)[0] == "lido"


def test_a_escada_normal_continua_subindo(pool):
    aid = _alvo(pool, "SM_ordem_5", status=None)
    _callback("SM_ordem_5", "sent")
    assert _status(pool, aid)[0] == "enviado"
    _callback("SM_ordem_5", "delivered")
    assert _status(pool, aid)[0] == "entregue"
    _callback("SM_ordem_5", "sent")            # não rebaixa
    assert _status(pool, aid)[0] == "entregue"
