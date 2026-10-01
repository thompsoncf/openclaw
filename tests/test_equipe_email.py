"""Corrigir o e-mail de um membro da equipe (contas.equipe.alterar_email).

O e-mail é o LOGIN da pessoa, então a troca leva o acesso junto:
- e-mail novo que já tem login no Zaq: o vínculo usa aquela senha (sem senha
  própria) e o convite pendente vira acesso ativo;
- membro com senha: entra com o e-mail novo e a mesma senha;
- sem senha (convite pendente): convite novo pro e-mail novo, o link antigo morre.
Nunca mexe no dono nem duplica e-mail dentro da mesma empresa.

Banco dedicado e descartável com o schema mínimo (mesmo padrão de
test_equipe_editar_excluir.py).
"""
import os

import pytest
from psycopg_pool import ConnectionPool

from contas import equipe as eq

_BASE_SQL = """
create table contas (id bigserial primary key, nome text, email text, senha_hash text,
  chip_de bigint);
create table membros (id bigserial primary key, conta_id bigint, nome text, papel text,
  email text, ativo boolean default true, convite_token text, convite_expira timestamptz,
  senha_hash text);
create unique index idx_membros_email_conta on membros (conta_id, lower(email))
  where email is not null;
"""


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_equipe_email_test"
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


def _conta(pool):
    with pool.connection() as c:
        cid = c.execute("insert into contas (nome) values ('Empresa') returning id").fetchone()[0]
        c.commit()
    return cid


def _membro(pool, conta, email, *, papel="vendedor", ativo=True, senha=None, token=None):
    with pool.connection() as c:
        mid = c.execute(
            """insert into membros (conta_id, nome, papel, email, ativo, senha_hash, convite_token)
               values (%s,'Emanuelly',%s,%s,%s,%s,%s) returning id""",
            (conta, papel, email, ativo, senha, token)).fetchone()[0]
        c.commit()
    return mid


def _linha(pool, mid):
    with pool.connection() as c:
        return c.execute(
            "select email, ativo, senha_hash, convite_token from membros where id=%s",
            (mid,)).fetchone()


def test_quem_tem_senha_entra_com_o_email_novo_e_a_mesma_senha(pool):
    conta = _conta(pool)
    m = _membro(pool, conta, "frotaarruda@gmail.com", senha="hash-dela")
    r = eq.alterar_email(pool, conta, m, "  EmanuellyFrotaArruda@gmail.com ")
    assert r["ok"] and r["acao"] == "mesma_senha" and r["token"] is None
    email, ativo, senha, token = _linha(pool, m)
    assert email == "emanuellyfrotaarruda@gmail.com" and ativo and senha == "hash-dela"


def test_convite_pendente_ganha_link_novo_e_o_antigo_morre(pool):
    conta = _conta(pool)
    m = _membro(pool, conta, "errado@gmail.com", ativo=False, token="link-antigo")
    r = eq.alterar_email(pool, conta, m, "certo@gmail.com")
    assert r["ok"] and r["acao"] == "convite" and r["token"] and r["token"] != "link-antigo"
    email, ativo, senha, token = _linha(pool, m)
    assert email == "certo@gmail.com" and token == r["token"] and not ativo
    assert eq.info_convite(pool, "link-antigo") is None            # o link errado não vale
    assert eq.info_convite(pool, r["token"])["email"] == "certo@gmail.com"


def test_vinculo_ativo_sem_senha_propria_ganha_convite_e_segue_na_fila(pool):
    """Quem entrou pela senha do e-mail antigo (vínculo sem senha nesta linha) precisa
    criar a senha do e-mail novo — e continua ativo (não sai do rodízio)."""
    conta = _conta(pool)
    m = _membro(pool, conta, "antigo@x.com", ativo=True)
    r = eq.alterar_email(pool, conta, m, "novo@x.com")
    assert r["acao"] == "convite"
    email, ativo, senha, token = _linha(pool, m)
    assert ativo is True and token == r["token"]


def test_email_novo_que_ja_tem_login_usa_a_senha_que_ja_existe(pool):
    from contas import senha as _senha
    outra = _conta(pool)
    _membro(pool, outra, "ja.tem@x.com", senha=_senha.hash_senha("segredo-123"))
    conta = _conta(pool)
    m = _membro(pool, conta, "digitado.errado@x.com", ativo=False, token="tok", senha=None)
    r = eq.alterar_email(pool, conta, m, "ja.tem@x.com")
    assert r["ok"] and r["acao"] == "login_existente"
    email, ativo, senha, token = _linha(pool, m)
    assert email == "ja.tem@x.com" and ativo is True and senha is None and token is None
    # a pessoa entra com a senha que já tinha e enxerga as duas empresas
    ctxs = {c["conta_id"] for c in eq.contextos_de_login(pool, "ja.tem@x.com", "segredo-123")}
    assert {outra, conta} <= ctxs


def test_email_com_login_tira_a_senha_propria_pra_nao_ter_duas(pool):
    conta = _conta(pool)
    with pool.connection() as c:
        c.execute("insert into contas (nome, email, senha_hash) values ('Dona', 'dona@x.com', 'h')")
        c.commit()
    m = _membro(pool, conta, "velho@x.com", senha="senha-da-linha")
    r = eq.alterar_email(pool, conta, m, "dona@x.com")
    assert r["acao"] == "login_existente" and _linha(pool, m)[2] is None


def test_recusas(pool):
    conta = _conta(pool)
    dono = _membro(pool, conta, "dono@x.com", papel="dono")
    a = _membro(pool, conta, "a@x.com", senha="h")
    _membro(pool, conta, "b@x.com", senha="h")
    assert not eq.alterar_email(pool, conta, dono, "outro@x.com")["ok"]      # dono não
    assert _linha(pool, dono)[0] == "dono@x.com"
    assert "outro membro" in eq.alterar_email(pool, conta, a, "B@x.com")["erro"]
    assert "inválido" in eq.alterar_email(pool, conta, a, "sem-arroba")["erro"]
    assert "inválido" in eq.alterar_email(pool, conta, a, "x@semponto")["erro"]
    assert "já é o e-mail" in eq.alterar_email(pool, conta, a, "A@X.com")["erro"]
    outra = _conta(pool)
    assert "não encontrado" in eq.alterar_email(pool, outra, a, "z@x.com")["erro"]  # outra empresa
    assert _linha(pool, a)[0] == "a@x.com"


def test_so_o_vinculo_desta_empresa_muda(pool):
    """A mesma pessoa em duas empresas: corrigir numa não mexe na outra."""
    c1, c2 = _conta(pool), _conta(pool)
    m1 = _membro(pool, c1, "pessoa@x.com", senha="h")
    m2 = _membro(pool, c2, "pessoa@x.com", senha="h")
    assert eq.alterar_email(pool, c1, m1, "pessoa.nova@x.com")["ok"]
    assert _linha(pool, m1)[0] == "pessoa.nova@x.com"
    assert _linha(pool, m2)[0] == "pessoa@x.com"
