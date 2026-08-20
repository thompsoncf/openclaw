"""prospeccao_campanha_config (POST .../campanhas/{id}/config): achado junto com o
vazamento do e-mail global — o checkbox "wa_ativo" salvava direto no banco sem
checar se a empresa REALMENTE tem WhatsApp conectado em canais_config. Como
whatsapp_out.enviar_template caía no número global do Zaq quando a empresa não
tinha número próprio, uma campanha "ativada" sem conectar nada saía pelo WhatsApp
de outra conta. Isso já está fechado na camada de envio (test_convites.py); este
teste cobre a camada de configuração: o servidor recusa `wa_ativo=on` se o canal
não estiver conectado, avisando o dono em vez de salvar em silêncio.

Banco dedicado e descartável com o schema MÍNIMO que a rota usa.
"""
import asyncio
import os

import pytest
from psycopg_pool import ConnectionPool

from web import painel_prospeccao as pp

_BASE_SQL = """
create table contas (id bigserial primary key, tipo text, nome text, chip_de bigint);
create table campanhas (id bigserial primary key, conta_id bigint, nome text, status text default 'ativa',
  limite_dia int default 40, limite_wa_dia int default 30, wa_ativo boolean default false,
  wa_mmlite boolean default false, wa_template_sid text, reengajar_ativo boolean default false,
  reengajar_dias int default 3, remetente_slot text default 'principal', teto_wa numeric(10,2),
  responsavel_id bigint,
  material text, material_tipo text default 'link', atualizado_em timestamptz default now());
create table canais_config (
  id bigserial primary key, conta_id bigint, canal text, identificador text,
  ativo boolean not null default true, token text, provedor text not null default 'twilio',
  wa_phone_id text, atualizado_em timestamptz default now());
"""


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_campanha_wa_gate_test"
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
    def __init__(self, form):
        self.session = {}
        self._form = form

    async def form(self):
        return self._form


def _campanha(pool, conta_id):
    with pool.connection() as c:
        cid = c.execute("insert into campanhas (conta_id, nome) values (%s,'C1') returning id",
                        (conta_id,)).fetchone()[0]
        c.commit()
    return cid


def _conta(pool, nome):
    with pool.connection() as c:
        cid = c.execute("insert into contas (tipo, nome) values ('pj',%s) returning id", (nome,)).fetchone()[0]
        c.commit()
    return cid


def test_ativar_wa_sem_conectar_e_recusado(pool, monkeypatch):
    # credenciais globais do Twilio presentes (isola o teste: a recusa é por causa
    # do canal desta EMPRESA não estar conectado, não por falta de config global)
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACxxx")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok")
    conta_id = _conta(pool, "Sem WhatsApp")
    camp_id = _campanha(pool, conta_id)
    monkeypatch.setattr(pp, "get_pool", lambda: pool)
    monkeypatch.setattr(pp, "_acesso", lambda req: (
        {"conta_id": conta_id, "gerencia": True}, None))
    req = _FakeRequest({"nome": "C1", "wa_ativo": "on"})
    resp = asyncio.run(pp.prospeccao_campanha_config(req, camp_id))
    assert resp.status_code == 303
    with pool.connection() as c:
        wa_ativo = c.execute("select wa_ativo from campanhas where id=%s", (camp_id,)).fetchone()[0]
    assert wa_ativo is False
    assert "conecte" in req.session["prosp_aviso"].lower()


def test_ativar_wa_com_canal_conectado_funciona(pool, monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACxxx")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok")
    conta_id = _conta(pool, "Com WhatsApp")
    camp_id = _campanha(pool, conta_id)
    with pool.connection() as c:
        c.execute("""insert into canais_config (conta_id, canal, identificador)
                      values (%s,'whatsapp','whatsapp:+5586990001111')""", (conta_id,))
        c.commit()
    monkeypatch.setattr(pp, "get_pool", lambda: pool)
    monkeypatch.setattr(pp, "_acesso", lambda req: (
        {"conta_id": conta_id, "gerencia": True}, None))
    req = _FakeRequest({"nome": "C1", "wa_ativo": "on"})
    resp = asyncio.run(pp.prospeccao_campanha_config(req, camp_id))
    assert resp.status_code == 303
    with pool.connection() as c:
        wa_ativo = c.execute("select wa_ativo from campanhas where id=%s", (camp_id,)).fetchone()[0]
    assert wa_ativo is True
    assert req.session["prosp_aviso"] == "Configuração salva ✓"
