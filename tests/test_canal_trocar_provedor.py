"""Trocar o provedor do WhatsApp pela tela precisa realmente salvar.

Bug encontrado em produção: o dono conectou o QR pra testar, quis voltar pro
Twilio, clicou em salvar várias vezes, a tela disse "salvo ✓" — e o banco não
mudava. As 7 campanhas dele ficaram paradas.

A causa: a rota fazia

    insert into canais_config (conta_id, canal, provedor, ativo)
    values (...) on conflict (conta_id, canal) do update set provedor=...

sem passar `identificador`, que é NOT NULL. O Postgres valida o NOT NULL ao montar
a tupla do INSERT, ANTES de resolver o conflito — então a instrução estourava
sempre, mesmo com a linha já existindo e só precisando do UPDATE. E o
`except Exception` genérico virava "Esse número já está vinculado a outra
empresa", ou, no caminho do WhatsApp, um "salvo ✓" que era mentira.

Efeito prático: quem conectava o QR não conseguia mais voltar pro Twilio pela
tela — e a prospecção fria fica bloqueada no QR (#404).
"""
import os

import pytest
from psycopg_pool import ConnectionPool

from web import painel_prospeccao as pp

_BASE_SQL = """
create table contas (id bigserial primary key, tipo text, nome text, chip_de bigint);
create table canais_config (
  id bigserial primary key, conta_id bigint, canal text,
  identificador text not null,                    -- o NOT NULL que quebrava o upsert
  ativo boolean not null default true, token text,
  provedor text not null default 'twilio', wa_phone_id text,
  tmpl_convite_sid text, tmpl_lembrete_sid text,
  atualizado_em timestamptz default now());
create unique index idx_canais_conta_canal on canais_config (conta_id, canal);
create unique index idx_canais_ident on canais_config (canal, identificador);
"""


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_canal_provedor_test"
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
    def __init__(self):
        self.session = {}


@pytest.fixture
def conta(pool, monkeypatch):
    with pool.connection() as c:
        cid = c.execute("insert into contas (tipo, nome) values ('pj','Teste') returning id"
                        ).fetchone()[0]
        c.commit()
    monkeypatch.setattr(pp, "get_pool", lambda: pool)
    monkeypatch.setattr(pp, "_acesso", lambda req: ({"conta_id": cid, "gerencia": True}, None))
    return cid


def _salvar(conta_id, provedor, numero=""):
    # chamada direta: os defaults da assinatura são objetos Form() do FastAPI, que
    # só viram string quando o framework resolve o request — passa todos na mão
    req = _FakeRequest()
    pp.comunicacao_canal_numero(req, canal="whatsapp", numero=numero, token="",
                                provedor=provedor, wa_phone_id="")
    return req.session.get("prosp_aviso", "")


def _canal(pool, conta_id):
    with pool.connection() as c:
        return c.execute("""select provedor, ativo, identificador from canais_config
                             where conta_id=%s and canal='whatsapp'""", (conta_id,)).fetchone()


def _com_canal(pool, conta_id, provedor, ident):
    with pool.connection() as c:
        c.execute("""insert into canais_config (conta_id, canal, identificador, provedor, ativo)
                      values (%s,'whatsapp',%s,%s,true)""", (conta_id, ident, provedor))
        c.commit()


def test_volta_do_qr_pro_twilio(pool, conta):
    """O caso exato da produção: canal em QR, desligado, e o dono querendo voltar."""
    _com_canal(pool, conta, "qr", f"whatsapp:+1760284{conta:04d}")
    with pool.connection() as c:
        c.execute("update canais_config set ativo=false where conta_id=%s", (conta,))
        c.commit()
    aviso = _salvar(conta, "twilio")
    prov, ativo, _ident = _canal(pool, conta)
    assert (prov, ativo) == ("twilio", True), f"não trocou (aviso foi: {aviso!r})"


def test_troca_sem_digitar_numero_mantem_o_que_estava(pool, conta):
    """A tela é não-destrutiva: campo vazio não apaga o número salvo."""
    ident = f"whatsapp:+1760285{conta:04d}"
    _com_canal(pool, conta, "qr", ident)
    _salvar(conta, "twilio", numero="")
    assert _canal(pool, conta) == ("twilio", True, ident)


def test_vai_e_volta_entre_provedores(pool, conta):
    _com_canal(pool, conta, "twilio", f"whatsapp:+1760286{conta:04d}")
    for prov in ("qr", "cloud", "twilio", "qr", "twilio"):
        _salvar(conta, prov)
        assert _canal(pool, conta)[0] == prov, f"travou ao ir pra {prov}"


def test_canal_novo_precisa_do_numero(pool, conta):
    """Sem linha ainda, o identificador é obrigatório (NOT NULL) — a rota avisa em
    vez de estourar."""
    aviso = _salvar(conta, "twilio", numero="")
    assert _canal(pool, conta) is None
    assert "número" in aviso.lower()


def test_canal_novo_com_numero_e_criado(pool, conta):
    _salvar(conta, "twilio", numero=f"+5586990{conta:05d}")
    prov, ativo, ident = _canal(pool, conta)
    assert (prov, ativo) == ("twilio", True)
    assert ident.startswith("whatsapp:+55")


def test_numero_de_outra_empresa_avisa_colisao(pool, conta, monkeypatch):
    """O índice único (canal, identificador) existe pra um número não servir duas
    empresas. A mensagem de colisão continua valendo — só não vale mais pra
    qualquer erro."""
    _com_canal(pool, conta, "twilio", "whatsapp:+5586999990000")
    with pool.connection() as c:
        outra = c.execute("insert into contas (tipo, nome) values ('pj','Outra') returning id"
                          ).fetchone()[0]
        c.execute("""insert into canais_config (conta_id, canal, identificador, provedor)
                      values (%s,'whatsapp','whatsapp:+5586999991111','twilio')""", (outra,))
        c.commit()
    aviso = _salvar(conta, "twilio", numero="+5586999991111")
    assert "outra empresa" in aviso.lower()
