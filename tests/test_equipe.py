"""Regressão do login por usuário + permissões + multi-empresa (contas.equipe).

Convite por link → membro cria senha → login com papel. Uma pessoa pode ter a
própria conta E ser membro de várias empresas com o MESMO e-mail: o login devolve
todos os contextos (troca de contexto). Escopo por conta_id. Banco de TESTE
separado (ver tests/conftest.py).
"""
import os

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from contas import equipe, senha as _senha


@pytest.fixture(scope="module")
def pool():
    p = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=4,
                       open=True, kwargs={"prepare_threshold": None})
    init_schema(p)
    equipe.garantir_tabela(p)   # colunas de login web + contas.senha_hash
    with p.connection() as c:   # banco compartilhado não trunca entre execuções
        c.execute("truncate contas cascade")
        c.commit()
    yield p
    p.close()


def _conta(pool, nome, email, senha=None):
    with pool.connection() as c:
        cid = c.execute(
            "insert into contas (tipo, nome, email, senha_hash) values ('pj',%s,%s,%s) returning id",
            (nome, email, _senha.hash_senha(senha) if senha else None)).fetchone()[0]
        c.commit()
    return cid


@pytest.fixture()
def conta_id(pool):
    # a EMPRESA que convida (o dono dela loga por conta).
    return _conta(pool, "Empresa A", "donoA@x.com", "donoApass")


def test_caps_por_papel():
    assert equipe.caps_do_papel("dono") == {"vendas": True, "financeiro": True, "gerir": True}
    assert equipe.caps_do_papel("vendedor") == {"vendas": True, "financeiro": False, "gerir": False}
    assert equipe.caps_do_papel("financeiro") == {"vendas": False, "financeiro": True, "gerir": False}
    assert equipe.caps_do_papel(None) == {"vendas": False, "financeiro": False, "gerir": False}


def test_aplicar_contexto():
    s = {}
    equipe.aplicar_contexto(s, {"conta_id": 5, "papel": "dono", "membro_id": None})
    assert s["conta_id"] == 5 and s["papel"] == "dono" and "membro_id" not in s
    equipe.aplicar_contexto(s, {"conta_id": 9, "papel": "vendedor", "membro_id": 3})
    assert s["conta_id"] == 9 and s["papel"] == "vendedor" and s["membro_id"] == 3


def test_convite_novo_usuario_aceite_e_login(pool, conta_id):
    r = equipe.convidar(pool, conta_id, "João", "joao@x.com", "vendedor")
    assert r["ok"] and r["token"] and r["ja_tem_login"] is False
    assert equipe.info_convite(pool, r["token"])["papel"] == "vendedor"

    assert equipe.aceitar_convite(pool, r["token"], "senha1234")["ok"]
    ctxs = equipe.contextos_de_login(pool, "joao@x.com", "senha1234")
    assert len(ctxs) == 1
    assert ctxs[0]["conta_id"] == conta_id and ctxs[0]["papel"] == "vendedor"
    assert ctxs[0]["membro_id"] == r["membro_id"]
    assert equipe.contextos_de_login(pool, "joao@x.com", "errada") == []


def test_pessoa_que_ja_tem_conta_vira_vendedor(pool, conta_id):
    """O caso do usuário: já é Zaq (conta própria) e vira vendedor de uma empresa."""
    pid = _conta(pool, "Conta do Zé", "ze@x.com", "zepass12")
    r = equipe.convidar(pool, conta_id, "Zé", "ze@x.com", "vendedor")
    assert r["ok"] and r["ja_tem_login"] is True and "token" not in r

    # Zé loga com a senha da conta DELE e vê os dois espaços.
    ctxs = equipe.contextos_de_login(pool, "ze@x.com", "zepass12")
    por_conta = {c["conta_id"]: c["papel"] for c in ctxs}
    assert len(ctxs) == 2
    assert por_conta[pid] == "dono" and por_conta[conta_id] == "vendedor"


def test_mesma_pessoa_em_duas_empresas(pool, conta_id):
    outra = _conta(pool, "Empresa B", "donoB@x.com", "donoBpas")
    r = equipe.convidar(pool, conta_id, "Maria", "maria@x.com", "vendedor")
    equipe.aceitar_convite(pool, r["token"], "senha1234")
    r2 = equipe.convidar(pool, outra, "Maria", "maria@x.com", "gestor")
    assert r2["ok"] and r2["ja_tem_login"] is True

    ctxs = equipe.contextos_de_login(pool, "maria@x.com", "senha1234")
    por_conta = {c["conta_id"]: c["papel"] for c in ctxs}
    assert len(ctxs) == 2          # membro das duas, sem conta própria
    assert por_conta[conta_id] == "vendedor" and por_conta[outra] == "gestor"


def test_duplicado_na_mesma_conta(pool, conta_id):
    equipe.convidar(pool, conta_id, "Bob", "bob@x.com", "vendedor")
    r = equipe.convidar(pool, conta_id, "Bob de novo", "bob@x.com", "gestor")
    assert r["ok"] is False and "equipe" in r["erro"].lower()


def test_papel_invalido(pool, conta_id):
    assert equipe.convidar(pool, conta_id, "X", "x@x.com", "chefe")["ok"] is False


def test_senha_curta_recusa(pool, conta_id):
    r = equipe.convidar(pool, conta_id, "Ana", "ana@x.com", "gestor")
    assert equipe.aceitar_convite(pool, r["token"], "curta")["ok"] is False


def test_atualizar_papel_e_desativar(pool, conta_id):
    r = equipe.convidar(pool, conta_id, "Bia", "bia@x.com", "vendedor")
    equipe.aceitar_convite(pool, r["token"], "senha1234")
    mid = r["membro_id"]

    assert equipe.atualizar_papel(pool, conta_id, mid, "financeiro")["ok"]
    assert equipe.contextos_de_login(pool, "bia@x.com", "senha1234")[0]["papel"] == "financeiro"
    assert equipe.atualizar_papel(pool, conta_id, mid, "chefe")["ok"] is False

    assert equipe.definir_ativo(pool, conta_id, mid, False)["ok"]
    assert equipe.contextos_de_login(pool, "bia@x.com", "senha1234") == []   # desativado some
    assert equipe.definir_ativo(pool, conta_id, mid, True)["ok"]
    assert len(equipe.contextos_de_login(pool, "bia@x.com", "senha1234")) == 1


def test_escopo_por_conta(pool, conta_id):
    outra = _conta(pool, "Outra", "d@outra.com", "outrapass")
    r = equipe.convidar(pool, conta_id, "Zeca", "zeca@x.com", "vendedor")
    mid = r["membro_id"]
    assert equipe.atualizar_papel(pool, outra, mid, "gestor")["ok"] is False
    assert equipe.definir_ativo(pool, outra, mid, False)["ok"] is False
    assert all(m["id"] != mid for m in equipe.listar_equipe(pool, outra))


def test_listar_equipe(pool, conta_id):
    # convite por LINK (e-mail novo, sem login) fica 'pendente' até aceitar
    equipe.convidar(pool, conta_id, "M1", "m1@x.com", "vendedor")
    eq = equipe.listar_equipe(pool, conta_id)
    assert any(m["email"] == "m1@x.com" and m["rotulo"] == "Vendedor" and m["pendente"] is True
               for m in eq)


def test_quem_ja_tem_login_nao_fica_pendente(pool, conta_id):
    """O caso do Rawilson: já tinha conta Zaq, foi convidado, entra ativo sem
    senha/token — NÃO pode aparecer como 'convite pendente'."""
    _conta(pool, "Conta do Rex", "rex2@x.com", "rexpass12")   # ele já tem login
    r = equipe.convidar(pool, conta_id, "Rex", "rex2@x.com", "vendedor")
    assert r["ok"] and r.get("ja_tem_login") is True
    m = next(x for x in equipe.listar_equipe(pool, conta_id) if x["email"] == "rex2@x.com")
    assert m["ativo"] is True and m["pendente"] is False


def test_regerar_convite(pool, conta_id):
    r = equipe.convidar(pool, conta_id, "Rex", "rex@x.com", "vendedor")
    equipe.aceitar_convite(pool, r["token"], "senha1234")
    novo = equipe.regerar_convite(pool, conta_id, r["membro_id"])
    assert novo["ok"] and novo["token"] != r["token"]
    assert equipe.contextos_de_login(pool, "rex@x.com", "senha1234") == []   # senha zerada
    assert equipe.info_convite(pool, novo["token"])["email"] == "rex@x.com"
