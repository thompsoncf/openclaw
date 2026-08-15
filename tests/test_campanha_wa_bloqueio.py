"""Campanha que não tem COMO disparar o WhatsApp frio: não pode queimar os alvos
nem sumir em silêncio.

Dois defeitos que andavam juntos:

1. **Alvo queimado.** Numa conta com o WhatsApp conectado por QR (sessão tipo
   WhatsApp Web, sem template), `enviar_template` devolve `provedor_sem_template`
   pra TODO alvo. O motor marcava `wa_status='erro'` em cada um — e a fila de
   disparo é `where wa_status is null`. Resultado: falha de configuração da CONTA
   tirava o lead da fila PRA SEMPRE, nem depois de conectar o Twilio ele voltava.

2. **Silêncio.** Campanha sem template era pulada com um `continue` seco: sem log,
   sem nada na tela. O dono achava que estava rodando.

A cura dos dois é a mesma: decidir uma vez por CAMPANHA (não alvo a alvo) e anotar
o motivo em `campanhas.wa_bloqueio`, que a tela lê.

Banco dedicado e descartável com o schema MÍNIMO que o motor usa.
"""
import os

import pytest
from psycopg_pool import ConnectionPool

from finance import campanhas_motor as cm
from finance import prospec_convite as pc

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
  alvo_telefone text, wa_tentados jsonb not null default '[]'::jsonb,
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
    dbname = "zaq_campanha_wa_bloqueio_test"
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


def _conta(pool, nome, provedor=None, identificador="whatsapp:+5586990001111"):
    with pool.connection() as c:
        cid = c.execute("insert into contas (tipo, nome) values ('pj',%s) returning id",
                        (nome,)).fetchone()[0]
        if provedor:
            c.execute("""insert into canais_config (conta_id, canal, identificador, provedor)
                          values (%s,'whatsapp',%s,%s)""", (cid, identificador, provedor))
        c.commit()
    return cid


def _campanha(pool, conta_id, sid=None, n_alvos=3):
    with pool.connection() as c:
        camp = c.execute("""insert into campanhas (conta_id, nome, wa_template_sid)
                             values (%s,'C',%s) returning id""", (conta_id, sid)).fetchone()[0]
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
        return c.execute("select wa_bloqueio, wa_bloqueio_em from campanhas where id=%s",
                         (camp,)).fetchone()


# --------------------------------------------------------------- motivo_bloqueio

def test_conta_no_qr_nao_dispara_frio(pool):
    """QR não tem template, e prospecção fria pelo número pessoal derruba a linha."""
    conta = _conta(pool, "QR", provedor="qr")
    with pool.connection() as c:
        assert pc.motivo_bloqueio(c, conta, "HXtemplate") == "provedor_qr"


def test_conta_sem_canal_nenhum(pool):
    conta = _conta(pool, "Sem canal")
    with pool.connection() as c:
        assert pc.motivo_bloqueio(c, conta, "HXtemplate") == "sem_canal"


def test_twilio_sem_sid_e_sem_credencial(pool, monkeypatch):
    monkeypatch.delenv("TWILIO_TMPL_PROSPEC_SID", raising=False)
    conta = _conta(pool, "Twilio pelado", provedor="twilio")
    with pool.connection() as c:
        assert pc.motivo_bloqueio(c, conta, None) == "sem_template"
        # com SID, o que falta são as credenciais do servidor
        monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
        monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
        assert pc.motivo_bloqueio(c, conta, "HXabc") == "sem_credenciais"


def test_twilio_completo_libera(pool, monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACxxx")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok")
    conta = _conta(pool, "Twilio ok", provedor="twilio")
    with pool.connection() as c:
        assert pc.motivo_bloqueio(c, conta, "HXabc") == ""


def test_cloud_com_template_libera_sem_credencial_twilio(pool, monkeypatch):
    """Cloud API é número próprio da empresa — não depende do Twilio do servidor."""
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    conta = _conta(pool, "Cloud", provedor="cloud")
    with pool.connection() as c:
        assert pc.motivo_bloqueio(c, conta, "meu_template") == ""


def test_toda_causa_de_bloqueio_tem_frase_pro_dono():
    """Um código sem rótulo vira um aviso vazio na tela."""
    for codigo in ("sem_canal", "provedor_qr", "sem_template", "sem_credenciais",
                   "sem_numero_empresa", "nao_configurado"):
        assert pc.BLOQUEIO_ROT.get(codigo), codigo


# ------------------------------------------------------------------- _wa_bloqueio

def test_bloqueio_grava_e_limpa(pool):
    conta = _conta(pool, "Anota", provedor="qr")
    camp = _campanha(pool, conta, n_alvos=0)
    cm._wa_bloqueio(pool, camp, "provedor_qr")
    motivo, quando = _bloqueio(pool, camp)
    assert motivo == "provedor_qr" and quando is not None
    cm._wa_bloqueio(pool, camp, "")
    motivo, quando = _bloqueio(pool, camp)
    assert motivo is None and quando is None


# ------------------------------------------------------ o motor não queima o alvo

class _FalhaDeConfig:
    """Provedor que recusa TODO envio, sempre com a mesma resposta."""

    def __init__(self, erro="provedor_sem_template", codigo=None, msg=None):
        self.erro, self.codigo, self.msg, self.tentativas = erro, codigo, msg, 0

    def enviar_template(self, c, conta_id, numero, sid, variaveis, mmlite=False):
        self.tentativas += 1
        return {"ok": False, "erro": self.erro, "codigo": self.codigo, "msg": self.msg}


@pytest.fixture
def motor_isolado(monkeypatch):
    """Isola o disparo do resto do funil: o que está em teste é o tratamento da
    falha, não a escolha do número nem o dedup de quem já respondeu."""
    monkeypatch.setattr(cm, "_respondeu", lambda pool, conta_id, pid: False)
    monkeypatch.setattr(cm, "_conta_identidade", lambda c, conta_id: {"empresa": "Zaq"})
    monkeypatch.setattr(cm, "fila_alvo_wa",
                        lambda c, conta_id, dados, alvo_tel, tentados=(): (
                            ["5586990002222"], "empresa"))


@pytest.mark.parametrize("erro", ["provedor_sem_template", "sem_numero_empresa",
                                  "nao_configurado", "sem_template"])
def test_falha_de_config_nao_queima_alvo_e_para_a_campanha(pool, motor_isolado, erro):
    conta = _conta(pool, f"Config {erro}", provedor="qr")
    camp = _campanha(pool, conta, sid="HXabc", n_alvos=3)
    prov = _FalhaDeConfig(erro)
    cm._disparar_wa_campanha(pool, camp, conta, "HXabc", 10, prov)
    # tentou UMA vez e parou — não insistiu nos outros dois
    assert prov.tentativas == 1
    # e ninguém saiu da fila
    assert _alvos(pool, camp) == [("(fila)", 3)]
    assert _bloqueio(pool, camp)[0] == erro


def test_twilio_20003_para_a_campanha_em_vez_de_queimar(pool, motor_isolado):
    """O caso que ACONTECEU: uma credencial errada no servidor devolveu 20003
    ("Unable to create record: Authenticate") e o motor marcou erro em 22 alvos
    de uma campanha, um a um, tirando todos da fila pra sempre."""
    conta = _conta(pool, "Twilio sem autenticar", provedor="twilio")
    camp = _campanha(pool, conta, sid="HXabc", n_alvos=3)
    prov = _FalhaDeConfig("Unable to create record: Authenticate", codigo=20003,
                          msg="Unable to create record: Authenticate")
    cm._disparar_wa_campanha(pool, camp, conta, "HXabc", 10, prov)
    assert prov.tentativas == 1
    assert _alvos(pool, camp) == [("(fila)", 3)]
    assert _bloqueio(pool, camp)[0] == "twilio_20003"
    assert "credenciais" in pc.rotulo_bloqueio("twilio_20003")


def test_codigo_2xxxx_desconhecido_ainda_tem_frase(pool):
    """Sem frase própria, o aviso não pode sair vazio na tela."""
    rot = pc.rotulo_bloqueio("twilio_20429")
    assert "20429" in rot and "Nenhum alvo foi gasto" in rot


@pytest.mark.parametrize("codigo", [63024, 63016, 63049])
def test_erro_63xxx_e_do_destinatario_e_continua_marcando(pool, motor_isolado, codigo):
    """Contraprova: 63xxx é do WhatsApp e vale por DESTINATÁRIO (63024 = o número
    não tem WhatsApp). Esse alvo falhou por mérito próprio — segue marcando e
    passando pro próximo, senão um número torto trava a campanha inteira."""
    conta = _conta(pool, f"Numero torto {codigo}", provedor="twilio")
    camp = _campanha(pool, conta, sid="HXabc", n_alvos=3)
    prov = _FalhaDeConfig("numero sem whatsapp", codigo=codigo)
    cm._disparar_wa_campanha(pool, camp, conta, "HXabc", 10, prov)
    assert prov.tentativas == 3
    assert _alvos(pool, camp) == [("erro", 3)]
    assert _bloqueio(pool, camp)[0] is None
