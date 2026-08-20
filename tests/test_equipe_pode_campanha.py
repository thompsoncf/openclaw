"""A liberação de campanhas por MEMBRO (migração 183 + contas.equipe).

Campanha dispara mensagem em massa pelo número da empresa. Até aqui só dono e
gestor criavam (o gate `gerencia` do painel de prospecção), e o vendedor — que é
quem está com o cliente e sabe qual recorte quer trabalhar — dependia de alguém
montar pra ele.

A liberação é por PESSOA, não por papel: um papel novo obrigaria a escolher entre
"todo vendedor pode" e "nenhum pode", e o risco aqui não é o cargo, é quantas
mãos podem disparar. O dono liga no botão do painel de Equipe, um a um.

O que estes testes travam, em ordem de gravidade:

1. NASCE DESLIGADO. Um deploy não pode dar poder novo a quem já está na equipe.
2. FALHA FECHADO. Erro de banco ou coluna ausente devolve False — nunca "pode".
3. NÃO ENCOSTA NO DONO, igual aos outros setters do módulo.
4. É POR CONTA. A mesma pessoa em duas empresas é liberada numa e não na outra.

Banco de TESTE separado (ver tests/conftest.py).
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
    equipe.garantir_tabela(p)          # cria/garante a coluna pode_campanha também
    with p.connection() as c:
        c.execute("truncate contas cascade")
        c.commit()
    yield p
    p.close()


def _conta(pool, nome, email):
    with pool.connection() as c:
        cid = c.execute(
            "insert into contas (tipo, nome, email, senha_hash) values ('pj',%s,%s,%s) returning id",
            (nome, email, _senha.hash_senha("senha-de-teste"))).fetchone()[0]
        c.commit()
    return cid


@pytest.fixture()
def conta_id(pool):
    return _conta(pool, "Empresa Teste", "dono-camp@exemplo.com")


@pytest.fixture()
def vendedor(pool, conta_id):
    r = equipe.convidar(pool, conta_id, "Vendedor", "vend-camp@exemplo.com", "vendedor")
    assert r["ok"], r
    return r["membro_id"]


# --- 1. nasce desligado ------------------------------------------------------

def test_membro_novo_nasce_sem_campanha(pool, conta_id, vendedor):
    """Ninguém ganha o poder de disparar em massa por causa de um deploy."""
    assert equipe.pode_campanha(pool, conta_id, vendedor) is False
    m = [x for x in equipe.listar_equipe(pool, conta_id) if x["id"] == vendedor][0]
    assert m["pode_campanha"] is False


# --- 2. o liga/desliga --------------------------------------------------------

def test_liga_e_desliga(pool, conta_id, vendedor):
    assert equipe.definir_pode_campanha(pool, conta_id, vendedor, True)["ok"]
    assert equipe.pode_campanha(pool, conta_id, vendedor) is True

    assert equipe.definir_pode_campanha(pool, conta_id, vendedor, False)["ok"]
    assert equipe.pode_campanha(pool, conta_id, vendedor) is False


def test_listar_equipe_mostra_o_estado(pool, conta_id, vendedor):
    """O botão do painel lê daqui pra saber se mostra 'Liberar' ou 'Campanhas ✓'."""
    equipe.definir_pode_campanha(pool, conta_id, vendedor, True)
    m = [x for x in equipe.listar_equipe(pool, conta_id) if x["id"] == vendedor][0]
    assert m["pode_campanha"] is True


# --- 3. escopo: conta e dono --------------------------------------------------

def test_nao_vaza_entre_empresas(pool, conta_id, vendedor):
    """Liberado numa empresa não é liberado na outra — nem dá pra liberar de fora."""
    outra = _conta(pool, "Outra Empresa", "dono2-camp@exemplo.com")
    equipe.definir_pode_campanha(pool, conta_id, vendedor, True)

    # a mesma pessoa, membro da outra empresa: vínculo diferente, flag separada
    r = equipe.convidar(pool, outra, "Vendedor", "vend-camp@exemplo.com", "vendedor")
    assert r["ok"], r
    assert equipe.pode_campanha(pool, outra, r["membro_id"]) is False
    # e o vínculo antigo segue liberado
    assert equipe.pode_campanha(pool, conta_id, vendedor) is True

    # tentar mexer no membro de outra conta não faz nada
    assert equipe.definir_pode_campanha(pool, outra, vendedor, False)["ok"] is False
    assert equipe.pode_campanha(pool, conta_id, vendedor) is True


def test_dono_nao_e_alvo(pool, conta_id):
    """Mesma blindagem dos outros setters: o titular não é gerido por aqui — e nem
    precisa, já passa pelo gate `gerencia`."""
    with pool.connection() as c:
        mid = c.execute(
            "insert into membros (conta_id, nome, papel, email, ativo) "
            "values (%s,'Titular','dono','titular-camp@exemplo.com',true) returning id",
            (conta_id,)).fetchone()[0]
        c.commit()
    assert equipe.definir_pode_campanha(pool, conta_id, mid, True)["ok"] is False
    assert equipe.pode_campanha(pool, conta_id, mid) is False


# --- 4. falha fechado ---------------------------------------------------------

def test_membro_id_vazio_nao_libera(pool, conta_id):
    """Login por CONTA (o dono) não tem membro_id. Cai em False aqui de propósito:
    o dono passa pelo `gerencia`, não por esta função."""
    for vazio in (None, 0, ""):
        assert equipe.pode_campanha(pool, conta_id, vazio) is False


def test_banco_fora_do_ar_nao_libera(conta_id):
    """Um erro de banco não pode virar permissão de disparar em massa."""
    class _PoolQuebrado:
        def connection(self):
            raise RuntimeError("banco fora do ar")
    assert equipe.pode_campanha(_PoolQuebrado(), conta_id, 999) is False


def test_membro_inexistente_nao_libera(pool, conta_id):
    assert equipe.pode_campanha(pool, conta_id, 10**9) is False
