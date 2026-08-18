"""Acesso do vendedor ao Cockpit: senha, "manter conectado" e revogação.

POR QUE ISTO EXISTE. Os vendedores relatavam sair sozinhos do app. A entrada do
Cockpit só oferecia LINK MÁGICO (15 min, uso pelo e-mail) — e é justamente a tela
onde a pessoa cai quando a sessão morre. Sem campo de senha ali, voltar dependia de
e-mail chegar e de abrir o link a tempo, no meio do expediente.

E-mail e senha JÁ existiam (`contas/equipe.py`, migração 072) e já mandavam o
vendedor pro Cockpit — só não eram oferecidos nesta porta. O que entrou foi expor
isso, mais a sessão indeterminada da migração 173.

O QUE ESTES TESTES TRAVAM:

  • senha errada e e-mail que não é do time dão a MESMA resposta — mensagem
    diferente entrega quem tem cadastro;
  • quem não é vendedor/gestor/dono não entra, mesmo com a senha certa;
  • o cookie "manter conectado" reconstrói a sessão sozinho — é o ponto do pedido
    ("deixa indeterminado"), e é o que substitui o prazo de 7 dias;
  • a validação RELÊ o membro: desativar alguém corta o acesso no request seguinte.
    Sem isso, uma sessão sem prazo seria um acesso vitalício a quem saiu da empresa;
  • SAIR SAI. Com um cookie que reconstrói sessão, esquecer de revogar faria o
    botão Sair não sair — o request seguinte entraria de novo;
  • a tabela guarda o HASH, nunca o token do cookie.

Roda com banco de TESTE separado (ver tests/conftest.py).
"""
import os
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from contas import senha as _senha
from db.conexao import init_schema
from finance import cockpit as ck

BASE = Path(__file__).resolve().parent.parent / "db" / "migracoes"


@pytest.fixture(scope="module")
def pool():
    p = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=4,
                       open=True, kwargs={"prepare_threshold": None})
    init_schema(p)
    with p.connection() as c:
        for mig in ("072_membro_login_web.sql", "134_cockpit_vendedor.sql",
                    "173_cockpit_lembrete.sql"):
            c.execute((BASE / mig).read_text(encoding="utf-8"))
        c.commit()
    yield p
    p.close()


@pytest.fixture()
def membro(pool):
    """Um vendedor com e-mail e senha, como Jacqueline e Pedro Yan na Prime."""
    with pool.connection() as c:
        cid = c.execute("insert into contas (tipo, nome) values ('pj','Prime') returning id"
                        ).fetchone()[0]
        mid = c.execute(
            """insert into membros (conta_id, nome, email, papel, ativo, senha_hash)
               values (%s,'Jacqueline','jac@prime.com','vendedor',true,%s) returning id""",
            (cid, _senha.hash_senha("segredo123"))).fetchone()[0]
        c.commit()
    yield cid, mid
    with pool.connection() as c:
        c.execute("delete from cockpit_lembrete where conta_id=%s", (cid,))
        c.execute("delete from membros where conta_id=%s", (cid,))
        c.execute("delete from contas where id=%s", (cid,))
        c.commit()


# --------------------------------------------------------------- senha

def test_entra_com_a_senha_certa(pool, membro):
    from contas import equipe as eq
    ctx = eq.autenticar(pool, "jac@prime.com", "segredo123")
    assert ctx and ctx["membro_id"] == membro[1]
    assert ctx["papel"] == "vendedor"


def test_senha_errada_nao_entra(pool, membro):
    from contas import equipe as eq
    assert eq.autenticar(pool, "jac@prime.com", "chute") is None


def test_membro_desativado_nao_entra_nem_com_a_senha_certa(pool, membro):
    from contas import equipe as eq
    cid, mid = membro
    with pool.connection() as c:
        c.execute("update membros set ativo=false where id=%s", (mid,))
        c.commit()
    assert eq.autenticar(pool, "jac@prime.com", "segredo123") is None


def test_definir_senha_grava_e_passa_a_valer(pool, membro):
    """A tela de "crie sua senha" grava na MESMA coluna do login web — uma
    credencial só pro vendedor, Cockpit e painel."""
    from contas import equipe as eq
    cid, mid = membro
    assert ck.definir_senha(pool, cid, mid, "outrasenha9")["ok"]
    assert eq.autenticar(pool, "jac@prime.com", "outrasenha9")
    assert eq.autenticar(pool, "jac@prime.com", "segredo123") is None


def test_senha_curta_e_recusada(pool, membro):
    cid, mid = membro
    r = ck.definir_senha(pool, cid, mid, "1234567")
    assert not r["ok"] and "8" in r["erro"]


def test_tem_senha_enxerga_quem_ainda_nao_criou(pool, membro):
    """É o que decide se a tela de criar senha aparece depois do link mágico."""
    cid, mid = membro
    assert ck.tem_senha(pool, cid, mid) is True
    with pool.connection() as c:
        c.execute("update membros set senha_hash=null where id=%s", (mid,))
        c.commit()
    assert ck.tem_senha(pool, cid, mid) is False


# ------------------------------------------------- manter conectado

def test_cookie_lembrado_vale_e_devolve_o_membro(pool, membro):
    cid, mid = membro
    tok = ck.lembrar_criar(pool, cid, mid, "iPhone")
    assert tok
    d = ck.lembrar_validar(pool, tok)
    assert d == {"conta_id": cid, "membro_id": mid, "papel": "vendedor"}


def test_a_tabela_guarda_o_hash_nunca_o_token(pool, membro):
    """Quem lê o banco não pode se passar por ninguém — mesma razão do senha_hash."""
    cid, mid = membro
    tok = ck.lembrar_criar(pool, cid, mid)
    with pool.connection() as c:
        guardado = c.execute("select token_hash from cockpit_lembrete where conta_id=%s",
                             (cid,)).fetchone()[0]
    assert guardado != tok
    assert len(guardado) == 64          # sha256 em hex


def test_token_inventado_nao_vale(pool, membro):
    assert ck.lembrar_validar(pool, "nao-sou-um-token") is None
    assert ck.lembrar_validar(pool, "") is None


def test_sair_revoga_o_aparelho(pool, membro):
    """SAIR TEM QUE SAIR. Com um cookie que reconstrói a sessão, esquecer de revogar
    faria o botão Sair virar enfeite: o request seguinte entrava de novo."""
    cid, mid = membro
    tok = ck.lembrar_criar(pool, cid, mid)
    assert ck.lembrar_validar(pool, tok)
    assert ck.lembrar_revogar(pool, tok) is True
    assert ck.lembrar_validar(pool, tok) is None


def test_sair_num_aparelho_nao_derruba_os_outros(pool, membro):
    cid, mid = membro
    celular, tablet = ck.lembrar_criar(pool, cid, mid), ck.lembrar_criar(pool, cid, mid)
    ck.lembrar_revogar(pool, celular)
    assert ck.lembrar_validar(pool, celular) is None
    assert ck.lembrar_validar(pool, tablet)


def test_desativar_o_membro_corta_todos_os_aparelhos(pool, membro):
    """A trava que uma sessão SEM PRAZO exige: sem ela, quem sai da empresa
    continuaria entrando pra sempre com o celular dele."""
    cid, mid = membro
    tok = ck.lembrar_criar(pool, cid, mid)
    with pool.connection() as c:
        c.execute("update membros set ativo=false where id=%s", (mid,))
        c.commit()
    assert ck.lembrar_validar(pool, tok) is None


def test_papel_fora_do_cockpit_nao_entra_pelo_cookie(pool, membro):
    cid, mid = membro
    tok = ck.lembrar_criar(pool, cid, mid)
    with pool.connection() as c:
        c.execute("update membros set papel='financeiro' where id=%s", (mid,))
        c.commit()
    assert ck.lembrar_validar(pool, tok) is None


def test_revogar_todos_os_aparelhos_do_membro(pool, membro):
    """Celular perdido / desligamento: corta tudo de uma vez, escopado por conta."""
    cid, mid = membro
    a, b = ck.lembrar_criar(pool, cid, mid), ck.lembrar_criar(pool, cid, mid)
    assert ck.lembrar_revogar_membro(pool, cid, mid) == 2
    assert ck.lembrar_validar(pool, a) is None
    assert ck.lembrar_validar(pool, b) is None


def test_lembrete_nao_tem_prazo(pool, membro):
    """O pedido foi "indeterminado": a tabela não tem coluna de expiração, e um
    lembrete antigo continua valendo. Se alguém acrescentar prazo, este teste cai."""
    cid, mid = membro
    tok = ck.lembrar_criar(pool, cid, mid)
    with pool.connection() as c:
        c.execute("""update cockpit_lembrete
                        set criado_em = now() - interval '5 years',
                            ultimo_uso = now() - interval '4 years'
                      where conta_id=%s""", (cid,))
        c.commit()
    assert ck.lembrar_validar(pool, tok), "lembrete de 5 anos atrás tem que valer"
