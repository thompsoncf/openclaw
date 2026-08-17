"""A campanha ficava CEGA no provedor que carrega o número próprio do cliente.

O adaptador do Twilio já lia o código do erro (`TwilioRestException.code`), e o
motor usava isso pra separar "problema da CONTA" de "problema DESTE número": 2xxxx
pára a campanha inteira, 63xxx queima só o alvo e a fila anda. Foi essa divisão que
impediu a repetição dos 22 alvos perdidos numa credencial errada, em agosto/2026.

A Cloud API nunca entrou nessa conta. `whatsapp_cloud._post` devolvia
`{"ok": False, "erro": <corpo cru>}` — sem `codigo`. Como `_erro_da_conta` só olha
`codigo`, TODA falha da Meta passava como se fosse do destinatário: token vencido,
número banido, conta travada, tanto faz. A campanha seguia queimando alvo por alvo,
e a fila é `wa_status is null` — quem sai, não volta.

Aqui a régua se INVERTE em relação ao Twilio, de propósito: na Meta o padrão é
bloquear, e só a lista curta de códigos do destinatário (`_META_ALVO`) segue
queimando o alvo. Um código novo da Meta pára a campanha em vez de gastar a base.
"""
import json
import os

import pytest
from psycopg_pool import ConnectionPool

from finance import campanhas_motor as cm
from finance import prospec_convite as pc
from finance import whatsapp_cloud as wc

_BASE_SQL = """
create table contas (id bigserial primary key, tipo text, nome text);
create table prospeccao (id bigserial primary key, conta_id bigint, empresa text,
  cnpj text, whatsapp text, telefone text, decisor_telefones jsonb, estagio text);
create table campanhas (id bigserial primary key, conta_id bigint, nome text,
  status text default 'ativa', wa_ativo boolean default true, limite_wa_dia int default 30,
  wa_enviados_hoje int default 0, wa_dia_contagem date, wa_template_sid text,
  wa_mmlite boolean default false, teto_wa numeric(10,2),
  wa_bloqueio text, wa_bloqueio_em timestamptz);
create table campanha_alvos (id bigserial primary key, campanha_id bigint, prospeccao_id bigint,
  status text default 'fila', wa_status text, wa_em timestamptz, wa_sid text,
  wa_erro_codigo text, wa_erro_msg text, wa_categoria text, wa_custo numeric(10,4),
  alvo_telefone text, wa_numero text, wa_tentados jsonb not null default '[]'::jsonb,
  wa_tentativas int not null default 0);
create table campanha_eventos (id bigserial primary key, campanha_id bigint, prospeccao_id bigint,
  canal text, evento text, detalhe text, quando timestamptz default now());
create table canais_config (
  id bigserial primary key, conta_id bigint, canal text, identificador text,
  ativo boolean not null default true, token text, provedor text not null default 'twilio',
  wa_phone_id text);
"""


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_campanha_wa_cloud_cega_test"
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


def _conta(pool, nome):
    with pool.connection() as c:
        cid = c.execute("insert into contas (tipo, nome) values ('pj',%s) returning id",
                        (nome,)).fetchone()[0]
        c.execute("""insert into canais_config (conta_id, canal, identificador, provedor, wa_phone_id, token)
                      values (%s,'whatsapp','whatsapp:+5586990001111','cloud','123','tok')""", (cid,))
        c.commit()
    return cid


def _campanha(pool, conta_id, n_alvos=3):
    with pool.connection() as c:
        camp = c.execute("""insert into campanhas (conta_id, nome, wa_template_sid)
                             values (%s,'C','meu_template') returning id""",
                         (conta_id,)).fetchone()[0]
        for i in range(n_alvos):
            pid = c.execute("""insert into prospeccao (conta_id, empresa, whatsapp, estagio)
                                values (%s,%s,'5586990002222','prospecto') returning id""",
                            (conta_id, f"Alvo {i}")).fetchone()[0]
            c.execute("insert into campanha_alvos (campanha_id, prospeccao_id) values (%s,%s)",
                      (camp, pid))
        c.commit()
    return camp


def _alvos(pool, camp):
    with pool.connection() as c:
        return c.execute("""select coalesce(wa_status,'(fila)'), count(*) from campanha_alvos
                             where campanha_id=%s group by 1""", (camp,)).fetchall()


def _bloqueio(pool, camp):
    with pool.connection() as c:
        return c.execute("select wa_bloqueio from campanhas where id=%s", (camp,)).fetchone()[0]


# ------------------------------------------------------- ler o código da Meta

def _corpo(code, message="deu ruim", subcode=None, details=None):
    err = {"code": code, "message": message}
    if subcode is not None:
        err["error_subcode"] = subcode
    if details is not None:
        err["error_data"] = {"details": details}
    return json.dumps({"error": err})


def test_codigo_e_mensagem_saem_do_corpo_da_meta():
    r = wc._erro_meta(_corpo(190, "Error validating access token", details="Session expired"))
    assert r["codigo"] == 190
    assert "access token" in r["msg"] and "Session expired" in r["msg"]


@pytest.mark.parametrize("corpo", ["", "não é json", "{}", '{"error":"texto"}',
                                   '{"error":{"message":"sem code"}}'])
def test_corpo_ilegivel_nao_inventa_codigo(corpo):
    """Melhor nada que um número chutado: quem chama trata a ausência de código."""
    assert wc._erro_meta(corpo) == {}


def test_mensagem_nunca_sai_vazia():
    """Um código sem texto viraria um erro mudo na tela do dono."""
    assert wc._erro_meta(_corpo(131031, "")).get("msg")


# ------------------------------------------------ de quem é a falha (_erro_da_conta)

@pytest.mark.parametrize("codigo", [190, 131031, 131042, 131048, 132000, 133010, 368])
def test_erro_de_conta_da_meta_bloqueia_a_campanha(codigo):
    res = {"ok": False, "erro": "x", "provedor": "cloud", "codigo": codigo}
    assert cm._erro_da_conta(res) == f"meta_{codigo}"


@pytest.mark.parametrize("codigo", sorted(cm._META_ALVO))
def test_erro_do_destinatario_da_meta_queima_so_o_alvo(codigo):
    """131026 = o número não recebe. É o 63024 da Meta: culpa do alvo, fila anda."""
    res = {"ok": False, "erro": "x", "provedor": "cloud", "codigo": codigo}
    assert cm._erro_da_conta(res) == ""


def test_falha_de_rede_na_cloud_nao_queima_ninguem():
    """Sem código nenhum (timeout, DNS, Graph fora do ar): o servidor não alcançou a
    Meta. O lead não tem nada com isso — parar é mais barato que gastar a base."""
    res = {"ok": False, "erro": "timed out", "provedor": "cloud", "msg": "timed out"}
    assert cm._erro_da_conta(res) == "meta_sem_codigo"


def test_twilio_nao_muda_de_regra():
    """Contraprova: a inversão vale só pra Meta. No Twilio, 63xxx segue do alvo e
    2xxxx segue da conta."""
    assert cm._erro_da_conta({"ok": False, "codigo": 63024}) == ""
    assert cm._erro_da_conta({"ok": False, "codigo": 20003}) == "twilio_20003"


def test_toda_causa_da_meta_tem_frase_pro_dono():
    for codigo in ("meta_190", "meta_131031", "meta_131042", "meta_131048",
                   "meta_sem_codigo"):
        assert pc.BLOQUEIO_ROT.get(codigo), codigo
    # e um código novo da Meta ainda sai com frase, não com aviso vazio
    rot = pc.rotulo_bloqueio("meta_133010")
    assert "133010" in rot and "Nenhum alvo foi gasto" in rot


# --------------------------------------------------- o motor, de ponta a ponta

class _CloudRecusa:
    """Cloud API que recusa TODO envio com o mesmo código da Meta."""

    def __init__(self, codigo):
        self.codigo, self.tentativas = codigo, 0

    def enviar_template(self, c, conta_id, numero, sid, variaveis, mmlite=False):
        self.tentativas += 1
        return {"ok": False, "erro": "recusado", "provedor": "cloud",
                "codigo": self.codigo, "msg": "recusado pela Meta"}


@pytest.fixture
def motor_isolado(monkeypatch):
    monkeypatch.setattr(cm, "_respondeu", lambda pool, conta_id, pid: False)
    monkeypatch.setattr(cm, "_conta_identidade", lambda c, conta_id: {"empresa": "Zaq"})
    monkeypatch.setattr(cm, "fila_alvo_wa",
                        lambda c, conta_id, dados, alvo_tel, tentados=(): (
                            ["5586990002222"], "empresa"))


def test_token_vencido_para_a_campanha_em_vez_de_queimar_a_base(pool, motor_isolado):
    """O 20003 do Twilio, agora do lado da Meta: 190 = token vencido. Antes disso
    ele queimaria os 3 alvos, um a um, e nenhum voltaria pra fila."""
    conta = _conta(pool, "Cloud token vencido")
    camp = _campanha(pool, conta, n_alvos=3)
    prov = _CloudRecusa(190)
    cm._disparar_wa_campanha(pool, camp, conta, "meu_template", 10, prov)
    assert prov.tentativas == 1          # parou no primeiro, não insistiu
    assert _alvos(pool, camp) == [("(fila)", 3)]
    assert _bloqueio(pool, camp) == "meta_190"


def test_numero_sem_whatsapp_na_meta_segue_queimando_o_alvo(pool, motor_isolado):
    """Contraprova no motor: 131026 é do destinatário, então a campanha NÃO pára."""
    conta = _conta(pool, "Cloud numero ruim")
    camp = _campanha(pool, conta, n_alvos=3)
    prov = _CloudRecusa(131026)
    cm._disparar_wa_campanha(pool, camp, conta, "meu_template", 10, prov)
    assert prov.tentativas == 3
    assert _alvos(pool, camp) == [("erro", 3)]
    assert _bloqueio(pool, camp) is None
