"""A fila de números precisa andar quando a falha vem pelo WEBHOOK, não só pela API.

O buraco que a produção mostrou: a fila só avançava quando o Twilio recusava na
hora da chamada (`enviar_template` devolvendo erro). O caso comum é o outro — ele
ACEITA, devolve um SID, e a entrega falha depois, por webhook, com 63024 ("este
número não tem WhatsApp"). Aí quem marcava `erro` era o webhook, que não mexia em
`wa_tentativas` nem em `wa_tentados`.

Cinco alvos ficaram assim: reenviados pro segundo número, falharam, e voltaram com
o contador em 1 e só o PRIMEIRO número riscado. Recolocá-los na fila mandaria pro
mesmo segundo número de novo, rodada após rodada, cada uma cobrada.

Meus testes anteriores cobriam só o caminho síncrono — testei o mecanismo que
escrevi, não o fluxo que a produção usa. Este arquivo cobre o assíncrono.
"""
import asyncio
import json
import os

import pytest
from psycopg_pool import ConnectionPool

from finance import campanhas_motor as cm
from web import painel_prospeccao as pp

_BASE_SQL = """
create table contas (id bigserial primary key, tipo text, nome text);
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
  provider_sid text, status text);
"""

_TELS = [{"formatado": "(86) 99900-0001", "provavel": True, "whatsapp": True, "tipo": "COMERCIAL"},
         {"formatado": "(86) 98800-0002", "provavel": False, "whatsapp": True, "tipo": "RESIDENCIAL"},
         {"formatado": "(86) 98800-0003", "provavel": False, "whatsapp": True, "tipo": "RESIDENCIAL"}]


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_wa_falha_entrega_test"
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


class _FakeRequest:
    class _Url:
        path = "/webhooks/twilio-status"

        def __str__(self):
            return "https://app.zaq-ia.com/webhooks/twilio-status"

    def __init__(self, params):
        self._params = params
        self.headers = {"X-Twilio-Signature": "sig", "host": "app.zaq-ia.com"}
        self.url = self._Url()

    async def form(self):
        return dict(self._params)


@pytest.fixture(autouse=True)
def ambiente(pool, monkeypatch):
    monkeypatch.setattr(pp, "get_pool", lambda: pool)
    from finance import whatsapp_twilio as wa
    monkeypatch.setattr(wa, "validar_assinatura", lambda url, params, sig: True)


def _alvo(pool, sid, numero, tentados=(), tentativas=0, alvo_telefone=None, tels=None):
    with pool.connection() as c:
        camp = c.execute("insert into campanhas (conta_id, nome) values (1,'C') returning id"
                         ).fetchone()[0]
        pid = c.execute("""insert into prospeccao (conta_id, empresa, decisor_telefones)
                            values (1,'Lead',%s::jsonb) returning id""",
                        (json.dumps(_TELS if tels is None else tels),)).fetchone()[0]
        aid = c.execute(
            """insert into campanha_alvos (campanha_id, prospeccao_id, wa_sid, wa_numero,
                                           wa_status, wa_tentados, wa_tentativas, alvo_telefone)
               values (%s,%s,%s,%s,'enviado',%s::jsonb,%s,%s) returning id""",
            (camp, pid, sid, numero, json.dumps(list(tentados)), tentativas,
             alvo_telefone)).fetchone()[0]
        c.commit()
    return aid


def _estado(pool, aid):
    with pool.connection() as c:
        return c.execute("""select wa_status, wa_tentativas, wa_tentados, wa_erro_codigo
                              from campanha_alvos where id=%s""", (aid,)).fetchone()


def _falhou(sid, codigo="63024"):
    return asyncio.run(pp.webhook_twilio_status(_FakeRequest({
        "MessageSid": sid, "MessageStatus": "failed",
        "ErrorCode": codigo, "ErrorMessage": "Number does not have WhatsApp"})))


def test_falha_de_entrega_risca_o_numero_e_devolve_pra_fila(pool):
    """O coração: com número sobrando, o alvo volta pra fila (wa_status null) e o
    número que falhou fica registrado — o motor pega o PRÓXIMO, não o mesmo."""
    aid = _alvo(pool, "SM_ent_1", "(86) 99900-0001")
    _falhou("SM_ent_1")
    status, tentativas, tentados, cod = _estado(pool, aid)
    assert status is None, "com número sobrando, volta pra fila"
    assert tentativas == 1
    assert tentados == ["86999000001"], "o número que falhou tem que ficar riscado"
    assert cod == "63024"


def test_segunda_falha_de_entrega_conta_de_novo(pool):
    """Era exatamente aqui que a produção travava: o 2º número falhava e o
    contador continuava em 1, com só o 1º riscado."""
    aid = _alvo(pool, "SM_ent_2", "(86) 98800-0002",
                tentados=["86999000001"], tentativas=1)
    _falhou("SM_ent_2")
    status, tentativas, tentados, _c = _estado(pool, aid)
    assert tentativas == 2
    assert tentados == ["86999000001", "86988000002"]
    assert status is None, "ainda sobra o 3º número"


def test_no_teto_para_de_vez(pool):
    aid = _alvo(pool, "SM_ent_3", "(86) 98800-0003",
                tentados=["86999000001", "86988000002"], tentativas=2)
    _falhou("SM_ent_3")
    status, tentativas, _t, _c = _estado(pool, aid)
    assert status == "erro" and tentativas == cm._WA_TENTATIVAS == 3


def test_fila_esgotada_para_mesmo_sem_bater_o_teto(pool):
    """Dois números só: acabou a fila no 2º, mesmo com uma tentativa sobrando."""
    aid = _alvo(pool, "SM_ent_4", "(86) 98800-0002", tentados=["86999000001"], tentativas=1,
                tels=_TELS[:2])
    _falhou("SM_ent_4")
    assert _estado(pool, aid)[0] == "erro"


def test_numero_travado_nao_ganha_fila(pool):
    """Escolha explícita do dono não se adivinha em cima."""
    aid = _alvo(pool, "SM_ent_5", "(86) 97777-7777", alvo_telefone="(86) 97777-7777")
    _falhou("SM_ent_5")
    status, tentativas, tentados, _c = _estado(pool, aid)
    assert status == "erro" and tentativas == 1
    assert tentados == ["86977777777"]


def test_mesmo_numero_com_55_nao_conta_duas_vezes(pool):
    """A normalização do #409 vale aqui também."""
    aid = _alvo(pool, "SM_ent_6", "+5586999000001")
    _falhou("SM_ent_6")
    assert _estado(pool, aid)[2] == ["86999000001"]


def test_callback_atrasado_nao_tira_da_fila_quem_voltou(pool):
    """Voltando pra fila, o SID da tentativa encerrada sai junto. Senão um
    "sent" atrasado daquele mesmo SID marcaria o alvo como 'enviado' e o tiraria
    da fila — ele ficaria esperando pra sempre um envio que não vai acontecer."""
    aid = _alvo(pool, "SM_ent_9", "(86) 99900-0001")
    _falhou("SM_ent_9")
    assert _estado(pool, aid)[0] is None
    with pool.connection() as c:
        assert c.execute("select wa_sid, wa_numero from campanha_alvos where id=%s",
                         (aid,)).fetchone() == (None, None)
    # o callback atrasado do SID antigo não acha mais ninguém
    asyncio.run(pp.webhook_twilio_status(_FakeRequest(
        {"MessageSid": "SM_ent_9", "MessageStatus": "sent"})))
    assert _estado(pool, aid)[0] is None, "o alvo tem que continuar na fila"


def test_alvo_que_parou_guarda_o_sid(pool):
    """Contraprova: quem encerrou mantém SID e número — é o rastro do que saiu."""
    aid = _alvo(pool, "SM_ent_10", "(86) 97777-7777", alvo_telefone="(86) 97777-7777")
    _falhou("SM_ent_10")
    with pool.connection() as c:
        sid, num = c.execute("select wa_sid, wa_numero from campanha_alvos where id=%s",
                             (aid,)).fetchone()
    assert sid == "SM_ent_10" and num == "(86) 97777-7777"


def test_entregue_nao_e_afetado(pool):
    """Erro que chega depois de entregue/lido não mexe em nada."""
    aid = _alvo(pool, "SM_ent_7", "(86) 99900-0001")
    with pool.connection() as c:
        c.execute("update campanha_alvos set wa_status='lido' where id=%s", (aid,))
        c.commit()
    _falhou("SM_ent_7")
    status, tentativas, tentados, _c = _estado(pool, aid)
    assert (status, tentativas, tentados) == ("lido", 0, [])


def test_evento_distingue_numero_que_falhou_de_alvo_encerrado(pool):
    """Na timeline: 'numero_falhou' enquanto anda, 'erro' quando para de vez."""
    aid = _alvo(pool, "SM_ent_8", "(86) 99900-0001")
    _falhou("SM_ent_8")
    with pool.connection() as c:
        ev = c.execute("""select e.evento from campanha_eventos e
                           join campanha_alvos a on a.campanha_id=e.campanha_id
                          where a.id=%s order by e.id desc limit 1""", (aid,)).fetchone()[0]
    assert ev == "numero_falhou"
