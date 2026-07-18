"""Regressão do login por usuário + permissões de equipe (contas.equipe).

Convite por link → membro cria a senha → login web com papel. Papéis são presets
de capacidade (vendas/financeiro/gerir). Multi-tenant: escopo por conta_id.
Roda com banco de TESTE separado (ver tests/conftest.py).
"""
import os

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from contas import equipe


@pytest.fixture(scope="module")
def pool():
    p = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=4,
                       open=True, kwargs={"prepare_threshold": None})
    init_schema(p)              # contas, membros
    equipe.garantir_tabela(p)   # colunas de login web
    # banco de teste é compartilhado e não trunca entre execuções — limpa aqui
    # pra os e-mails (únicos por índice) não colidirem num re-run.
    with p.connection() as c:
        c.execute("truncate contas cascade")
        c.commit()
    yield p
    p.close()


@pytest.fixture()
def conta_id(pool):
    with pool.connection() as c:
        cid = c.execute(
            "insert into contas (tipo, nome, email) values ('pj','Aladdin','dono@aladdin.com') returning id"
        ).fetchone()[0]
        c.commit()
    return cid


def test_caps_por_papel():
    assert equipe.caps_do_papel("dono") == {"vendas": True, "financeiro": True, "gerir": True}
    assert equipe.caps_do_papel("vendedor") == {"vendas": True, "financeiro": False, "gerir": False}
    assert equipe.caps_do_papel("financeiro") == {"vendas": False, "financeiro": True, "gerir": False}
    assert equipe.caps_do_papel(None) == {"vendas": False, "financeiro": False, "gerir": False}


def test_convite_aceite_e_login(pool, conta_id):
    r = equipe.convidar(pool, conta_id, "João", "joao@aladdin.com", "vendedor")
    assert r["ok"] and r["token"]

    # antes de aceitar: não loga (sem senha), mas o convite é consultável
    assert equipe.autenticar(pool, "joao@aladdin.com", "qualquer") is None
    info = equipe.info_convite(pool, r["token"])
    assert info and info["email"] == "joao@aladdin.com" and info["papel"] == "vendedor"

    # aceita e define a senha
    ac = equipe.aceitar_convite(pool, r["token"], "senha1234")
    assert ac["ok"] and ac["papel"] == "vendedor" and ac["conta_id"] == conta_id

    # agora loga
    ctx = equipe.autenticar(pool, "joao@aladdin.com", "senha1234")
    assert ctx and ctx["membro_id"] == r["membro_id"] and ctx["papel"] == "vendedor"
    # senha errada não passa; token já não serve mais
    assert equipe.autenticar(pool, "joao@aladdin.com", "errada") is None
    assert equipe.info_convite(pool, r["token"]) is None


def test_senha_curta_recusa(pool, conta_id):
    r = equipe.convidar(pool, conta_id, "Ana", "ana@aladdin.com", "gestor")
    assert equipe.aceitar_convite(pool, r["token"], "curta")["ok"] is False


def test_papel_invalido_e_email_duplicado(pool, conta_id):
    assert equipe.convidar(pool, conta_id, "X", "x@aladdin.com", "chefe")["ok"] is False
    assert equipe.convidar(pool, conta_id, "Dup", "dono@aladdin.com", "gestor")["ok"] is False  # é e-mail de conta
    equipe.convidar(pool, conta_id, "Y", "y@aladdin.com", "vendedor")
    assert equipe.convidar(pool, conta_id, "Y2", "y@aladdin.com", "vendedor")["ok"] is False     # já usado


def test_atualizar_papel_e_desativar(pool, conta_id):
    r = equipe.convidar(pool, conta_id, "Bia", "bia@aladdin.com", "vendedor")
    equipe.aceitar_convite(pool, r["token"], "senha1234")
    mid = r["membro_id"]

    assert equipe.atualizar_papel(pool, conta_id, mid, "financeiro")["ok"]
    assert equipe.autenticar(pool, "bia@aladdin.com", "senha1234")["papel"] == "financeiro"
    assert equipe.atualizar_papel(pool, conta_id, mid, "chefe")["ok"] is False

    # desativar corta o login
    assert equipe.definir_ativo(pool, conta_id, mid, False)["ok"]
    assert equipe.autenticar(pool, "bia@aladdin.com", "senha1234") is None
    assert equipe.definir_ativo(pool, conta_id, mid, True)["ok"]
    assert equipe.autenticar(pool, "bia@aladdin.com", "senha1234") is not None


def test_escopo_por_conta(pool, conta_id):
    with pool.connection() as c:
        outra = c.execute(
            "insert into contas (tipo, nome, email) values ('pj','Outra','d@outra.com') returning id"
        ).fetchone()[0]
        c.commit()
    r = equipe.convidar(pool, conta_id, "Zé", "ze@aladdin.com", "vendedor")
    mid = r["membro_id"]
    # a outra conta não gerencia o membro desta
    assert equipe.atualizar_papel(pool, outra, mid, "gestor")["ok"] is False
    assert equipe.definir_ativo(pool, outra, mid, False)["ok"] is False
    assert all(m["id"] != mid for m in equipe.listar_equipe(pool, outra))


def test_listar_equipe(pool, conta_id):
    equipe.convidar(pool, conta_id, "M1", "m1@aladdin.com", "vendedor")
    eq = equipe.listar_equipe(pool, conta_id)
    assert any(m["email"] == "m1@aladdin.com" and m["rotulo"] == "Vendedor" for m in eq)
    # convidado ainda não aceitou
    assert any(m["email"] == "m1@aladdin.com" and m["aceitou"] is False for m in eq)


def test_regerar_convite(pool, conta_id):
    r = equipe.convidar(pool, conta_id, "Rex", "rex@aladdin.com", "vendedor")
    equipe.aceitar_convite(pool, r["token"], "senha1234")
    novo = equipe.regerar_convite(pool, conta_id, r["membro_id"])
    assert novo["ok"] and novo["token"] != r["token"]
    # regerar invalida o login antigo até re-aceitar
    assert equipe.autenticar(pool, "rex@aladdin.com", "senha1234") is None
    assert equipe.info_convite(pool, novo["token"])["email"] == "rex@aladdin.com"
