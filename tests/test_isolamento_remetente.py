"""remetente_conta()/enviar_conta() (finance/email_inbound.py) nunca podem cair no
SMTP_USER global quando a empresa não configurou a própria caixa — esse env é o
e-mail de SISTEMA do Zaq (achado em produção: apontava pro e-mail real de um
cliente, e aparecia/enviava como se fosse de QUALQUER outra conta sem caixa
própria). Mesma classe de bug em whatsapp_out.enviar_template com
TWILIO_WHATSAPP_FROM, coberta em test_convites.py.

Banco dedicado e descartável com o schema MÍNIMO (mesmo padrão dos outros testes
de blindagem) — não replica migrações antigas.
"""
import os

import pytest
from psycopg_pool import ConnectionPool

from finance import email_inbound as ei

_BASE_SQL = """
create table contas (id bigserial primary key, tipo text, nome text, chip_de bigint);
create table canais_config (
  id bigserial primary key, conta_id bigint, canal text, identificador text,
  ativo boolean not null default true, imap_senha text, imap_host text,
  ultimo_uid bigint, token text, provedor text not null default 'twilio',
  wa_phone_id text, atualizado_em timestamptz default now());
"""


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_isolamento_remetente_test"
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


@pytest.fixture()
def conta_sem_caixa(pool):
    with pool.connection() as c:
        cid = c.execute("insert into contas (tipo, nome) values ('pj','Sem Caixa') returning id").fetchone()[0]
        c.commit()
    return cid


@pytest.fixture()
def conta_com_caixa(pool):
    with pool.connection() as c:
        cid = c.execute("insert into contas (tipo, nome) values ('pj','Com Caixa') returning id").fetchone()[0]
        c.execute("""insert into canais_config (conta_id, canal, identificador, imap_senha, imap_host)
                      values (%s,'email','contato@empresa-b.com.br','senha123','imap.gmail.com')""", (cid,))
        c.commit()
    return cid


def test_sem_caixa_propria_nao_usa_smtp_global(pool, conta_sem_caixa, monkeypatch):
    """Mesmo com o SMTP global do Zaq configurado no env, uma conta sem caixa
    própria não pode aparecer/enviar como se aquele e-mail fosse dela."""
    monkeypatch.setenv("SMTP_USER", "outra-empresa-real@dominio-do-cliente.com.br")
    monkeypatch.setenv("SMTP_SENHA", "segredo-do-outro-cliente")
    assert ei.remetente_conta(pool, conta_sem_caixa) is None
    assert ei.remetente_conta(pool, conta_sem_caixa, "email2") is None


def test_caixa_propria_ignora_env_global(pool, conta_com_caixa, monkeypatch):
    monkeypatch.setenv("SMTP_USER", "outra-empresa-real@dominio-do-cliente.com.br")
    monkeypatch.setenv("SMTP_SENHA", "segredo-do-outro-cliente")
    assert ei.remetente_conta(pool, conta_com_caixa) == "contato@empresa-b.com.br"


def test_enviar_conta_sem_caixa_nao_dispara_pelo_smtp_global(pool, conta_sem_caixa, monkeypatch):
    monkeypatch.setenv("SMTP_USER", "outra-empresa-real@dominio-do-cliente.com.br")
    monkeypatch.setenv("SMTP_SENHA", "segredo-do-outro-cliente")
    from finance import email_sender as es
    chamou = {}
    monkeypatch.setattr(es, "enviar_email", lambda *a, **k: chamou.setdefault("foi", True) or True)
    ok = ei.enviar_conta(pool, conta_sem_caixa, "lead@exemplo.com", "Oi", "<p>oi</p>")
    assert ok is False
    assert "foi" not in chamou   # nunca chamou o SMTP global


def test_enviar_conta_com_caixa_usa_credencial_propria(pool, conta_com_caixa, monkeypatch):
    from finance import email_sender as es
    capt = {}
    monkeypatch.setattr(es, "enviar_com_creds",
                        lambda user, senha, host, port, *a, **k: capt.update(user=user, senha=senha) or True)
    ok = ei.enviar_conta(pool, conta_com_caixa, "lead@exemplo.com", "Oi", "<p>oi</p>")
    assert ok is True
    assert capt["user"] == "contato@empresa-b.com.br"
    assert capt["senha"] == "senha123"
