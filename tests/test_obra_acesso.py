"""A entrada do mestre de obras sem e-mail e sem senha: cadastro na Equipe e link
mágico pelo WhatsApp (finance/obra_acesso.py; pedido do dono em 03/10/2026).

`test_link_de_mestre_nao_abre_o_cockpit_e_vice_versa` é a trava que importa: as
tabelas são as do Cockpit, e as validações não podem se cruzar.
"""
import os
from pathlib import Path
from urllib.parse import unquote

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool
from starlette.middleware.sessions import SessionMiddleware

from contas import equipe as eq
from db.conexao import init_schema
from finance import cockpit as ck
from finance import obra_acesso as oa
from finance import obra_campo as oc
from finance import obras as ob
from web import app_obra as ao
from web import painel_equipe as pe

_MIGRACOES = ("053_modulo_pj.sql", "057_natureza_lancamento.sql", "058_dados_empresa.sql",
              "064_clientes_lojista.sql", "066_pessoas_identidade.sql", "067_titulos_cliente.sql",
              "072_membro_login_web.sql", "131_pessoa_cnpj.sql", "132_plano_contas_centros_custo.sql",
              "134_cockpit_vendedor.sql", "173_cockpit_lembrete.sql",
              "143_plano_contas_locacao_buffet_servicos.sql", "182_clientes_papel.sql",
              "186_plano_aporte_socios.sql", "195_titulo_aprovacao.sql",
              "196_titulo_recorrencia.sql", "197_titulo_acrescimo.sql",
              "317_titulo_classificacao.sql", "325_tipo_despesa.sql",
              "336_plano_fardamentos.sql", "349_plano_obras.sql", "351_obras.sql",
              "353_obra_venda_documentos.sql", "355_reforma_orcamento.sql",
              "365_obras_lembrete_segunda.sql", "367_pix_da_empresa.sql", "369_obra_fotos.sql",
              "371_obra_etapa_pagamentos.sql", "478_obra_quadras.sql", "550_obra_mestre_e_campo.sql")
_BASE = Path(__file__).resolve().parent.parent / "db" / "migracoes"


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_obra_acesso_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname} with (force)")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=4, open=True, kwargs={"prepare_threshold": None})
    init_schema(p)
    for m in _MIGRACOES:
        with p.connection() as c:
            c.execute((_BASE / m).read_text(encoding="utf-8"))
            c.commit()
    eq.garantir_tabela(p)          # as colunas de login (whatsapp...) que nascem em runtime
    yield p
    p.close()


@pytest.fixture()
def conta(pool):
    with pool.connection() as c:
        cid = c.execute("insert into contas (tipo, nome, nome_fantasia) values "
                        "('pj', 'Pablo', 'PX2 Empreendimentos') returning id").fetchone()[0]
        c.commit()
    return cid


@pytest.fixture(autouse=True)
def _url(monkeypatch):
    monkeypatch.setattr(oa, "_app_url", lambda: "https://app.zaq-ia.com")


def _token(link: str) -> str:
    return link.rsplit("/", 1)[1]


# ── o cadastro e o link ───────────────────────────────────────────────────
def test_fone_vira_formato_do_wa_me():
    assert oa.fone_br("(86) 99999-8888") == "5586999998888"
    assert oa.fone_br("+55 86 99999-8888") == "5586999998888"
    assert oa.fone_br("86 3232-1010") == "558632321010"
    assert oa.fone_br("") == ""


def test_cadastra_sem_email_e_aparece_na_equipe(pool, conta):
    mid = oa.cadastrar_mestre(pool, conta, "José da Silva", "(86) 99999-8888")
    m = next(x for x in eq.listar_equipe(pool, conta) if x["id"] == mid)
    assert m["papel"] == "mestre" and m["ativo"] and not m["pendente"] and m["email"] is None
    assert m["whatsapp"] == "5586999998888"
    with pytest.raises(ValueError, match="Já tem"):
        oa.cadastrar_mestre(pool, conta, "Outro", "86 99999-8888")
    with pytest.raises(ValueError, match="DDD"):
        oa.cadastrar_mestre(pool, conta, "Sem fone", "9999")


def test_link_entra_lembra_o_celular_e_link_novo_mata_o_velho(pool, conta):
    mid = oa.cadastrar_mestre(pool, conta, "José da Silva", "86 98888-7777")
    r = oa.gerar_link(pool, conta, mid, "PX2 Empreendimentos")
    assert r["link"].startswith("https://app.zaq-ia.com/obra/entrar/")
    assert r["whatsapp_url"].startswith("https://wa.me/5586988887777?text=")
    msg = unquote(r["whatsapp_url"].split("text=", 1)[1])
    assert msg.startswith("Oi José!") and "PX2 Empreendimentos" in msg and r["link"] in msg
    t1 = _token(r["link"])
    assert oa.validar_token(pool, t1)["membro_id"] == mid
    assert oa.validar_token(pool, t1)                              # reusável no prazo (prévia do WhatsApp)
    t2 = _token(oa.gerar_link(pool, conta, mid)["link"])
    assert oa.validar_token(pool, t1) is None and oa.validar_token(pool, t2)


def test_desativar_corta_link_e_celular_lembrado(pool, conta):
    mid = oa.cadastrar_mestre(pool, conta, "Zé", "86 97777-6666")
    t = _token(oa.gerar_link(pool, conta, mid)["link"])
    lembrete = oa.lembrar(pool, conta, mid)
    assert oa.lembrete_valido(pool, lembrete)["membro_id"] == mid
    with pool.connection() as c:
        c.execute("update membros set ativo=false where id=%s", (mid,))
        c.commit()
    assert oa.validar_token(pool, t) is None and oa.lembrete_valido(pool, lembrete) is None
    with pytest.raises(ValueError, match="desativado"):
        oa.gerar_link(pool, conta, mid)


def test_link_de_mestre_nao_abre_o_cockpit_e_vice_versa(pool, conta):
    mid = oa.cadastrar_mestre(pool, conta, "Zé Cockpit", "86 96666-5555")
    t = _token(oa.gerar_link(pool, conta, mid)["link"])
    assert ck.validar_token(pool, t) is None                       # o Cockpit não aceita mestre
    with pool.connection() as c:
        vend = c.execute("insert into membros (conta_id, nome, papel, ativo) values (%s,'V','vendedor',true) "
                         "returning id", (conta,)).fetchone()[0]
        c.commit()
    tv = ck.gerar_token(pool, conta, vend)
    assert oa.validar_token(pool, tv) is None                      # e o /obra não aceita vendedor
    lv = ck.lembrar_criar(pool, conta, vend)
    assert oa.lembrete_valido(pool, lv) is None
    with pytest.raises(ValueError, match="não é mestre"):
        oa.gerar_link(pool, conta, vend)


# ── o app: entrar pelo link, voltar lembrado, sair de verdade ─────────────
def _app(pool, monkeypatch):
    monkeypatch.setattr(ao, "get_pool", lambda: pool)
    monkeypatch.setattr(ao, "conta_logada",
                        lambda request: (request.session.get("conta_id"),) + (None,) * 16
                        if request.session.get("conta_id") else None)
    monkeypatch.setattr(ao, "nicho_da_conta", lambda c: "construcao")
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="t")
    app.include_router(ao.router)
    return TestClient(app, follow_redirects=False)


def test_entra_pelo_link_e_o_celular_fica_lembrado(pool, conta, monkeypatch):
    mid = oa.cadastrar_mestre(pool, conta, "Zé App", "86 95555-4444")
    o = ob.criar_obra(pool, conta, "Casa do Zé", "casa")
    oc.definir_mestre(pool, conta, o["id"], mid)
    c = _app(pool, monkeypatch)
    assert c.get("/obra").headers["location"] == "/obra/sem-acesso"     # sem link, sem entrada
    t = _token(oa.gerar_link(pool, conta, mid)["link"])
    r = c.get(f"/obra/entrar/{t}")
    assert r.status_code == 303 and r.headers["location"] == "/obra"
    assert oa.COOKIE in r.cookies
    assert "Casa do Zé" in c.get("/obra").text
    # sessão perdida (o celular reiniciou): o cookie entra de novo, calado
    lembrete = r.cookies[oa.COOKIE]
    c2 = _app(pool, monkeypatch)
    c2.cookies.set(oa.COOKIE, lembrete, path="/obra")
    assert "Casa do Zé" in c2.get("/obra").text
    # sair DE VERDADE: o cookie não entra mais
    c2.get("/obra/sair")
    c3 = _app(pool, monkeypatch)
    c3.cookies.set(oa.COOKIE, lembrete, path="/obra")
    assert c3.get("/obra").headers["location"] == "/obra/sem-acesso"
    assert "venceu" in c.get("/obra/sem-acesso?expirou=1").text
    assert c.get("/obra/entrar/token-que-nao-existe").headers["location"].endswith("expirou=1")


# ── a Equipe ──────────────────────────────────────────────────────────────
def _equipe(pool, conta, monkeypatch, nicho="construcao"):
    monkeypatch.setattr(pe, "get_pool", lambda: pool)
    linha = [conta, "pj", "PX2 Empreendimentos"] + [None] * 14
    monkeypatch.setattr(pe, "conta_logada", lambda request: tuple(linha))
    monkeypatch.setattr(pe, "nicho_da_conta", lambda c: nicho)
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="t")
    app.include_router(pe.router)

    @app.post("/_entrar")
    async def _entrar(request: Request):
        request.session["papel"] = "dono"
        return {"ok": True}

    c = TestClient(app, follow_redirects=False)
    c.post("/_entrar", json={})
    return c


def test_equipe_cadastra_o_mestre_e_mostra_o_botao_do_whatsapp(pool, conta, monkeypatch):
    c = _equipe(pool, conta, monkeypatch)
    r = c.post("/painel/equipe/mestre", data={"nome": "Seu Zé", "whatsapp": "86 94444-3333"})
    assert r.headers["location"] == "/painel/equipe#link-mestre"
    mid = next(m["id"] for m in eq.listar_equipe(pool, conta) if m["nome"] == "Seu Zé")
    assert mid
    # o link e o wa.me estão na sessão que a tela consome (sem renderizar o portal todo)
    with pool.connection() as cx:
        assert cx.execute("select count(*) from cockpit_acesso where membro_id=%s",
                          (mid,)).fetchone()[0] == 1
    r = c.post(f"/painel/equipe/mestre/{mid}/link")
    assert r.headers["location"] == "/painel/equipe#link-mestre"
    with pool.connection() as cx:
        assert cx.execute("select count(*) from cockpit_acesso where membro_id=%s",
                          (mid,)).fetchone()[0] == 1               # o novo substituiu o velho


def test_equipe_fora_da_construcao_nao_cadastra_mestre(pool, conta, monkeypatch):
    c = _equipe(pool, conta, monkeypatch, nicho="clinica")
    c.post("/painel/equipe/mestre", data={"nome": "Intruso", "whatsapp": "86 93333-2222"})
    assert not [m for m in eq.listar_equipe(pool, conta) if m["nome"] == "Intruso"]
