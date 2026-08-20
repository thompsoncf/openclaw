"""Os três provedores têm que chegar no MESMO lugar quando a entrega falha.

São payloads bem diferentes — Twilio manda form do StatusCallback, o Cloud API
manda `statuses[]` no webhook da Meta, o QR manda itens do serviço Node — e cada
um tinha (ou ia ter) a sua própria cópia da regra. Foi assim que o Cloud API
ficou sem NUNCA marcar entregue/lido/erro no alvo da campanha: o webhook dele só
lia o `pricing` pra corrigir custo, e o `parse_status_whatsapp` ignora de
propósito quem não traz preço — que é justamente o status `failed`.

Consequência numa conta Cloud API: KPIs de entregue/lido zerados e a fila de
números nunca andando por falha de entrega, que é o caso comum.

Agora cada webhook só traduz o payload dele em (sid, status, erro) e chama
`aplicar_status_wa`. Estes testes travam essa equivalência.
"""
import os

import pytest
from psycopg_pool import ConnectionPool

from finance import meta_msg
from web import painel_prospeccao as pp

_BASE_SQL = """
create table prospeccao (id bigserial primary key, conta_id bigint, empresa text,
  cnpj text, whatsapp text, telefone text, decisor_telefones jsonb);
create table campanhas (id bigserial primary key, conta_id bigint, nome text);
create table campanha_alvos (id bigserial primary key, campanha_id bigint, prospeccao_id bigint,
  wa_sid text, wa_numero text, wa_status text, wa_em timestamptz,
  wa_erro_codigo text, wa_erro_msg text, alvo_telefone text,
  wa_tentados jsonb not null default '[]'::jsonb, wa_tentativas int not null default 0);
create table campanha_eventos (id bigserial primary key, campanha_id bigint, prospeccao_id bigint,
  canal text, evento text, detalhe text, quando timestamptz default now());
create table mensagens (id bigserial primary key, conversa_id bigint, canal text,
  -- `meta` existe em produção desde a migração 080 e é onde o MOTIVO da falha
  -- é gravado (o código do provedor, a mensagem dele). Faltava aqui, e o
  -- update caía com UndefinedColumn — schema mínimo que esqueceu uma coluna
  -- que a produção tem.
  provider_sid text, status text, meta jsonb);
"""

_TELS = '[{"formatado":"(86) 99900-0001","provavel":true,"whatsapp":true,"tipo":"COMERCIAL"},' \
        ' {"formatado":"(86) 98800-0002","provavel":false,"whatsapp":true,"tipo":"RESIDENCIAL"}]'


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_wa_tres_provedores_test"
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


@pytest.fixture(autouse=True)
def ambiente(pool, monkeypatch):
    monkeypatch.setattr(pp, "get_pool", lambda: pool)


def _alvo(pool, sid, numero="(86) 99900-0001"):
    with pool.connection() as c:
        camp = c.execute("insert into campanhas (conta_id, nome) values (1,'C') returning id"
                         ).fetchone()[0]
        pid = c.execute("""insert into prospeccao (conta_id, empresa, decisor_telefones)
                            values (1,'Lead',%s::jsonb) returning id""", (_TELS,)).fetchone()[0]
        aid = c.execute("""insert into campanha_alvos (campanha_id, prospeccao_id, wa_sid,
                                                       wa_numero, wa_status)
                            values (%s,%s,%s,%s,'enviado') returning id""",
                        (camp, pid, sid, numero)).fetchone()[0]
        c.commit()
    return aid


def _estado(pool, aid):
    with pool.connection() as c:
        return c.execute("""select wa_status, wa_tentativas, wa_tentados
                              from campanha_alvos where id=%s""", (aid,)).fetchone()


def _meta_payload(sid, status, codigo=None):
    st = {"id": sid, "status": status}
    if codigo:
        st["errors"] = [{"code": codigo, "title": "Message undeliverable"}]
    return {"object": "whatsapp_business_account",
            "entry": [{"changes": [{"value": {"statuses": [st]}}]}]}


# ------------------------------------------------------------------- o parser

def test_parser_do_cloud_pega_o_failed_sem_pricing():
    """`parse_status_whatsapp` ignora quem não traz preço — e `failed` não traz.
    Era por isso que a falha do Cloud API não chegava em lugar nenhum."""
    payload = _meta_payload("wamid.X", "failed", codigo=131026)
    assert meta_msg.parse_status_whatsapp(payload) == [], "o parser de custo ignora, como sempre"
    entrega = meta_msg.parse_entrega_whatsapp(payload)
    assert entrega == [{"sid": "wamid.X", "status": "erro",
                        "erro_codigo": "131026", "erro_msg": "Message undeliverable"}]


@pytest.mark.parametrize("bruto,esperado", [
    ("sent", "enviado"), ("delivered", "entregue"), ("read", "lido"), ("failed", "erro"),
])
def test_parser_do_cloud_fala_o_vocabulario_do_app(bruto, esperado):
    """O mesmo vocabulário que o Twilio produz — senão `aplicar_status_wa` teria
    que conhecer os dois dialetos."""
    r = meta_msg.parse_entrega_whatsapp(_meta_payload("wamid.Y", bruto))
    assert r[0]["status"] == esperado


def test_parser_do_cloud_ignora_payload_de_outro_produto():
    assert meta_msg.parse_entrega_whatsapp({"object": "page", "entry": []}) == []


# ----------------------------------------------- Twilio e Cloud API, lado a lado

def test_falha_do_cloud_anda_a_fila_igual_ao_twilio(pool):
    """O ponto do arquivo: mesmo efeito, payloads diferentes."""
    a_twilio = _alvo(pool, "SM_tw")
    a_cloud = _alvo(pool, "wamid.cloud")

    with pool.connection() as c:
        pp.aplicar_status_wa(c, "SM_tw", "erro", "63024", "sem whatsapp")
        c.commit()
    with pool.connection() as c:
        for ent in meta_msg.parse_entrega_whatsapp(
                _meta_payload("wamid.cloud", "failed", codigo=131026)):
            pp.aplicar_status_wa(c, ent["sid"], ent["status"], ent["erro_codigo"], ent["erro_msg"])
        c.commit()

    st_tw, tent_tw, tentados_tw = _estado(pool, a_twilio)
    st_cl, tent_cl, tentados_cl = _estado(pool, a_cloud)
    assert (st_tw, tent_tw, tentados_tw) == (None, 1, ["86999000001"])
    assert (st_cl, tent_cl, tentados_cl) == (None, 1, ["86999000001"]), (
        "o Cloud API não marcava nada no alvo — a fila nunca andava numa conta Cloud"
    )


def test_entrega_do_cloud_sobe_o_status_do_alvo(pool):
    """KPIs de Entregues/Lidos ficavam zerados numa conta Cloud API."""
    aid = _alvo(pool, "wamid.ok")
    with pool.connection() as c:
        for bruto in ("delivered", "read"):
            for ent in meta_msg.parse_entrega_whatsapp(_meta_payload("wamid.ok", bruto)):
                pp.aplicar_status_wa(c, ent["sid"], ent["status"],
                                     ent["erro_codigo"], ent["erro_msg"])
        c.commit()
    assert _estado(pool, aid)[0] == "lido"


def test_cloud_tambem_respeita_a_escada(pool):
    """'sent' atrasado não rebaixa o que já foi entregue — mesma trava do Twilio."""
    aid = _alvo(pool, "wamid.ordem")
    with pool.connection() as c:
        for bruto in ("delivered", "sent"):
            for ent in meta_msg.parse_entrega_whatsapp(_meta_payload("wamid.ordem", bruto)):
                pp.aplicar_status_wa(c, ent["sid"], ent["status"],
                                     ent["erro_codigo"], ent["erro_msg"])
        c.commit()
    assert _estado(pool, aid)[0] == "entregue"


# ------------------------------------------------- a FIAÇÃO, não só as peças
# Os testes acima exercitam parse + aplicar direto. Passariam mesmo se ninguém
# tivesse ligado os dois dentro do webhook — que foi exatamente o defeito
# original: as peças existiam, a rota do Cloud API não chamava. Este vai pela
# rota de verdade.

def test_a_rota_do_cloud_api_realmente_liga_o_parser_no_aplicador(pool, monkeypatch):
    import asyncio
    import json as _json
    aid = _alvo(pool, "wamid.rota")
    payload = _meta_payload("wamid.rota", "failed", codigo=131026)
    corpo = _json.dumps(payload).encode()

    class _Req:
        headers = {"x-hub-signature-256": "sha256=x"}

        async def body(self):
            return corpo

    monkeypatch.setattr(meta_msg, "validar_assinatura", lambda b, s: True)

    class _BG:
        def add_task(self, *a, **k):
            pass

    asyncio.run(pp.webhook_meta(_Req(), _BG()))
    status, tentativas, tentados = _estado(pool, aid)
    assert (status, tentativas, tentados) == (None, 1, ["86999000001"]), (
        "a rota do Cloud API precisa aplicar o status de entrega, não só o custo"
    )


def test_o_qr_nao_dispara_campanha_entao_nao_tem_essa_via(pool):
    """Contraprova documental: a campanha fria é bloqueada em conta QR (#404), então
    não existe alvo de campanha com SID do QR pra este caminho tratar. Se um dia o
    QR ganhar campanha, é `aplicar_status_wa` que ele tem que chamar — não uma
    quarta cópia da regra."""
    from finance import prospec_convite as pc
    assert pc.BLOQUEIO_ROT["provedor_qr"]
