"""Login do Cockpit usa a identidade UNIFICADA por e-mail, igual ao portal.

O QUE ACONTECEU (18/08). A pessoa trocou a senha duas vezes, entrou e saiu, e o
Cockpit continuou recusando. O mesmo e-mail existia em DOIS lugares:

    contas  id 15 — conta própria, COM senha
    membros id 41 — "zaq teste", vendedor da Prime, SEM senha nenhuma

Ela trocava a senha da CONTA. O Cockpit chamava `equipe.autenticar`, que só olha
`membros.senha_hash` — nulo — e devolvia "e-mail ou senha incorretos". Pelo painel
entrava, porque o portal já usa `contextos_de_login`.

`contextos_de_login` é a autoridade certa: valida a senha contra a conta própria e,
passando, devolve TODOS os lugares onde a pessoa trabalha — inclusive vínculos de
membro que não têm senha própria. É o desenho "uma senha, vários contextos", e o
Cockpit estava fora dele.

E QUEM TRABALHA EM MAIS DE UM LUGAR ESCOLHE. Entrar na empresa errada é pior que um
toque a mais: o vendedor mexeria no funil de outra empresa achando que é o dele.

Sem rede: banco de teste próprio, criado e destruído aqui.
"""
import os
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from contas import senha as _senha
from db.conexao import init_schema

BASE = Path(__file__).resolve().parent.parent / "db" / "migracoes"


@pytest.fixture(scope="module")
def pool():
    p = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=4,
                       open=True, kwargs={"prepare_threshold": None})
    init_schema(p)
    with p.connection() as c:
        for mig in ("072_membro_login_web.sql", "073_membro_multi_empresa.sql",
                    "134_cockpit_vendedor.sql", "173_cockpit_lembrete.sql"):
            c.execute((BASE / mig).read_text(encoding="utf-8"))
        c.commit()
    yield p
    p.close()


@pytest.fixture()
def cenario(pool, monkeypatch):
    """O caso real: e-mail que é conta (com senha) E membro vendedor (sem senha)."""
    with pool.connection() as c:
        cid_propria = c.execute(
            """insert into contas (tipo, nome, email, senha_hash)
               values ('pj','Thompsoncf','t@h.com',%s) returning id""",
            (_senha.hash_senha("minhasenha1"),)).fetchone()[0]
        cid_prime = c.execute(
            "insert into contas (tipo, nome) values ('pj','Prime Eventos') returning id"
        ).fetchone()[0]
        mid = c.execute(
            """insert into membros (conta_id, nome, email, papel, ativo)
               values (%s,'zaq teste','t@h.com','vendedor',true) returning id""",
            (cid_prime,)).fetchone()[0]
        c.commit()
    from web import painel_cockpit as pc
    monkeypatch.setattr(pc, "get_pool", lambda: pool)
    yield {"conta_propria": cid_propria, "conta_prime": cid_prime, "membro": mid}
    with pool.connection() as c:
        c.execute("delete from cockpit_lembrete")
        c.execute("delete from membros where lower(email)='t@h.com'")
        c.execute("delete from contas where id in (%s,%s)", (cid_propria, cid_prime))
        c.commit()


def _cli():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from starlette.middleware.sessions import SessionMiddleware
    from web.painel_cockpit import router
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="teste")
    app.include_router(router)
    return TestClient(app)


def test_a_senha_da_conta_abre_o_cockpit(cenario):
    """O DEFEITO EM SI. O membro não tem senha própria; a autoridade é a conta."""
    r = _cli().post("/cockpit/login", data={"email": "t@h.com", "senha": "minhasenha1"},
                    follow_redirects=False)
    assert r.status_code == 200
    assert "Onde você vai trabalhar" in r.text
    assert "incorreto" not in r.text


def test_mostra_os_dois_lugares_pra_escolher(cenario):
    r = _cli().post("/cockpit/login", data={"email": "t@h.com", "senha": "minhasenha1"},
                    follow_redirects=False)
    assert "Thompsoncf" in r.text and "Prime Eventos" in r.text


def test_escolher_a_empresa_entra_nela(cenario, pool):
    cli = _cli()
    cli.post("/cockpit/login", data={"email": "t@h.com", "senha": "minhasenha1",
                                     "lembrar": "1"}, follow_redirects=False)
    r = cli.post("/cockpit/empresa", data={"i": "1"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/cockpit"
    with pool.connection() as c:
        n = c.execute("select count(*) from cockpit_lembrete where membro_id=%s",
                      (cenario["membro"],)).fetchone()[0]
    assert n == 1, "marcou 'manter conectado': tem que lembrar o aparelho"


def test_indice_fora_da_lista_nao_entra_em_lugar_nenhum(cenario):
    """O índice vem do formulário, mas quem limita é a lista que a SENHA abriu."""
    cli = _cli()
    cli.post("/cockpit/login", data={"email": "t@h.com", "senha": "minhasenha1"},
             follow_redirects=False)
    r = cli.post("/cockpit/empresa", data={"i": "99"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("/login")


def test_escolher_empresa_sem_ter_logado_nao_entra(cenario):
    """Sem passar pelo login não há lista na sessão — e sem lista não há entrada."""
    r = _cli().post("/cockpit/empresa", data={"i": "0"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("/login")


def test_senha_errada_continua_recusando(cenario):
    r = _cli().post("/cockpit/login", data={"email": "t@h.com", "senha": "chute"},
                    follow_redirects=False)
    assert r.status_code == 200 and "incorretos" in r.text


def test_um_lugar_so_entra_direto_sem_perguntar(pool, monkeypatch):
    """Quem trabalha num lugar só não vê tela de escolha — seria um toque à toa."""
    with pool.connection() as c:
        cid = c.execute("insert into contas (tipo,nome) values ('pj','Só Aqui') returning id"
                        ).fetchone()[0]
        c.execute("""insert into membros (conta_id,nome,email,papel,ativo,senha_hash)
                     values (%s,'Solo','solo@x.com','vendedor',true,%s)""",
                  (cid, _senha.hash_senha("senha12345")))
        c.commit()
    from web import painel_cockpit as pc
    monkeypatch.setattr(pc, "get_pool", lambda: pool)
    try:
        r = _cli().post("/cockpit/login", data={"email": "solo@x.com",
                                                "senha": "senha12345"},
                        follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/cockpit"
    finally:
        with pool.connection() as c:
            c.execute("delete from cockpit_lembrete")
            c.execute("delete from membros where lower(email)='solo@x.com'")
            c.execute("delete from contas where id=%s", (cid,))
            c.commit()


# ------------------------------------------- uma senha por PESSOA, não por vínculo
#
# A autoridade é a CONTA. Quem tem conta no Zaq entra com a senha dela em qualquer
# empresa onde seja membro. Só quem NÃO tem conta precisa de senha própria no
# vínculo. Sem isso, a mesma pessoa acabava com DUAS senhas válidas — e trocar uma
# não mexia na outra, que é exatamente como alguém trocou a senha duas vezes e o app
# continuou recusando.

def test_quem_tem_conta_nao_e_convidado_a_criar_outra_senha(cenario, pool):
    """`tem_senha` responde sobre a PESSOA: ela já tem como entrar."""
    from finance import cockpit as ck
    assert ck.tem_senha(pool, cenario["conta_prime"], cenario["membro"]) is True


def test_quem_tem_conta_nao_consegue_gravar_uma_segunda_senha(cenario, pool):
    from finance import cockpit as ck
    r = ck.definir_senha(pool, cenario["conta_prime"], cenario["membro"], "outrasenha9")
    assert r["ok"] is False and r.get("ja_tem_conta") is True
    with pool.connection() as c:
        h = c.execute("select senha_hash from membros where id=%s",
                      (cenario["membro"],)).fetchone()[0]
    assert not h, "não pode ter gravado senha no vínculo de quem já tem conta"


def test_a_senha_da_conta_continua_valendo_depois_da_recusa(cenario):
    """O ponto: ela não fica sem acesso — só não ganha uma senha paralela."""
    cli = _cli()
    r = cli.post("/cockpit/login", data={"email": "t@h.com", "senha": "minhasenha1"},
                 follow_redirects=False)
    assert r.status_code == 200 and "Onde você vai trabalhar" in r.text


def test_quem_NAO_tem_conta_cria_e_usa_a_senha_do_vinculo(pool, monkeypatch):
    """O outro lado da regra: vendedor sem conta no Zaq precisa de senha própria."""
    from finance import cockpit as ck
    from contas import equipe as eq
    with pool.connection() as c:
        cid = c.execute("insert into contas (tipo,nome) values ('pj','Sem Conta') returning id"
                        ).fetchone()[0]
        mid = c.execute("""insert into membros (conta_id,nome,email,papel,ativo)
                           values (%s,'Novato','novato@x.com','vendedor',true) returning id""",
                        (cid,)).fetchone()[0]
        c.commit()
    try:
        assert ck.tem_senha(pool, cid, mid) is False      # ainda não tem como entrar
        assert ck.definir_senha(pool, cid, mid, "senha12345")["ok"] is True
        assert ck.tem_senha(pool, cid, mid) is True
        assert eq.autenticar(pool, "novato@x.com", "senha12345")
    finally:
        with pool.connection() as c:
            c.execute("delete from membros where id=%s", (mid,))
            c.execute("delete from contas where id=%s", (cid,))
            c.commit()
