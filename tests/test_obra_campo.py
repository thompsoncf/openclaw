"""O app do mestre de obras (web/app_obra.py, finance/obra_campo.py, migração 550;
desenho docs/mockups/obras_mapa_3d.html, seção 4).

`test_o_app_nao_mostra_dinheiro` é o contrato da decisão 4 do dono: nenhuma tela
do app pode ter "R$" — nem custo, nem previsto, nem o pagamento da empreitada.
"""
import os
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool
from starlette.middleware.sessions import SessionMiddleware

from contas import equipe as eq
from db.conexao import init_schema
from finance import obra_campo as oc
from finance import obra_grupos as og
from finance import obra_material as omat
from finance import obras as ob
from web import app_obra as ao
from web import painel_obras as po

_MIGRACOES = ("016_unidade_medida.sql", "018_chave_nfce_lancamentos.sql", "019_codigo_gtin.sql",
              "032_catalogo_estoque.sql", "053_modulo_pj.sql", "057_natureza_lancamento.sql",
              "058_dados_empresa.sql", "060_unidade_generica.sql", "064_clientes_lojista.sql",
              "066_pessoas_identidade.sql", "067_titulos_cliente.sql", "072_membro_login_web.sql",
              "131_pessoa_cnpj.sql", "132_plano_contas_centros_custo.sql",
              "143_plano_contas_locacao_buffet_servicos.sql", "182_clientes_papel.sql",
              "186_plano_aporte_socios.sql", "195_titulo_aprovacao.sql",
              "196_titulo_recorrencia.sql", "197_titulo_acrescimo.sql",
              "317_titulo_classificacao.sql", "325_tipo_despesa.sql",
              "336_plano_fardamentos.sql", "349_plano_obras.sql", "351_obras.sql",
              "353_obra_venda_documentos.sql", "355_reforma_orcamento.sql",
              "365_obras_lembrete_segunda.sql", "367_pix_da_empresa.sql", "369_obra_fotos.sql",
              "371_obra_etapa_pagamentos.sql", "478_obra_quadras.sql", "482_obra_mapas.sql",
              "484_obra_material.sql", "550_obra_mestre_e_campo.sql")
_BASE = Path(__file__).resolve().parent.parent / "db" / "migracoes"


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_obra_campo_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname} with (force)")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=4, open=True, kwargs={"prepare_threshold": None})
    init_schema(p)
    with p.connection() as c:
        c.execute("create table if not exists precos_observados (id bigserial primary key, item_id bigint)")
        c.commit()
    for m in _MIGRACOES:
        with p.connection() as c:
            c.execute((_BASE / m).read_text(encoding="utf-8"))
            c.commit()
    yield p
    p.close()


@pytest.fixture()
def conta(pool):
    with pool.connection() as c:
        cid = c.execute("insert into contas (tipo, nome, nome_fantasia) values "
                        "('pj', 'Pablo', 'PX2 Empreendimentos') returning id").fetchone()[0]
        c.commit()
    return cid


def _membro(pool, conta, papel="mestre", nome="Seu Zé"):
    with pool.connection() as c:
        mid = c.execute("insert into membros (conta_id, nome, papel, ativo) values (%s,%s,%s,true) "
                        "returning id", (conta, nome, papel)).fetchone()[0]
        c.commit()
    return mid


def _obra(pool, conta, nome, mestre=None, etapas=("Fundação", "Alvenaria", "Cobertura")):
    o = ob.criar_obra(pool, conta, nome, "casa", custo_previsto_centavos=8_000_000,
                      valor_centavos=15_000_000)
    ob.salvar_etapas(pool, conta, o["id"], [(None, e, round(100 / len(etapas))) for e in etapas])
    if mestre:
        oc.definir_mestre(pool, conta, o["id"], mestre)
    return ob.obter_obra(pool, conta, o["id"])


# ── o papel ───────────────────────────────────────────────────────────────
def test_papel_mestre_so_tem_o_campo():
    caps = eq.caps_do_papel("mestre")
    assert caps["campo"] and not (caps["vendas"] or caps["financeiro"] or caps["gerir"] or caps["origens"])
    assert eq.home_do_papel("mestre", 7) == "/obra"
    assert eq.destino_barrado("mestre") == "/obra" in eq.rotas_do_papel("mestre")
    assert not any(r.startswith("/painel/obras") for r in eq.rotas_do_papel("mestre"))
    assert "mestre" in eq.PAPEIS_PJ and eq.rotulo("mestre") == "Mestre de obras"
    # quem já existia não ganha nem perde nada além do campo
    assert not eq.caps_do_papel("vendedor")["campo"] and eq.caps_do_papel("gestor")["campo"]


def test_mestre_so_aparece_na_construcao(monkeypatch):
    from web import painel_equipe as pe
    monkeypatch.setattr(pe, "nicho_da_conta", lambda c: "construcao")
    assert "mestre" in pe._papeis_da_conta(("x",))
    monkeypatch.setattr(pe, "nicho_da_conta", lambda c: "clinica")
    assert "mestre" not in pe._papeis_da_conta(("x",))
    assert "vendedor" in pe._papeis_da_conta(("x",))


# ── quem vê o quê ─────────────────────────────────────────────────────────
def test_mestre_ve_so_as_obras_dele(pool, conta):
    ze, joao = _membro(pool, conta), _membro(pool, conta, nome="João")
    a = _obra(pool, conta, "Casa A", mestre=ze)
    b = _obra(pool, conta, "Casa B", mestre=joao)
    _obra(pool, conta, "Casa C")
    assert [o["nome"] for o in oc.obras_do(pool, conta, "mestre", ze)] == ["Casa A"]
    assert oc.pode(pool, conta, "mestre", ze, a["id"]) and not oc.pode(pool, conta, "mestre", ze, b["id"])
    assert len(oc.obras_do(pool, conta, "gestor", None)) == 3           # o gestor cobre todos
    vend = _membro(pool, conta, papel="vendedor", nome="Vendedor")
    with pytest.raises(ValueError, match="mestre de obras"):
        oc.definir_mestre(pool, conta, a["id"], vend)                   # só papel mestre


# ── os gestos e o desfazer ────────────────────────────────────────────────
def test_marcar_etapa_conta_no_andamento_e_desfaz(pool, conta):
    ze = _membro(pool, conta)
    o = _obra(pool, conta, "Casa E", mestre=ze)
    r = oc.marcar_etapa(pool, conta, o["id"], o["etapas"][0]["id"], ze)
    assert "Fundação marcada" in r["frase"] and ob.obter_obra(pool, conta, o["id"])["pct"] > 0
    with pytest.raises(ValueError, match="já estava"):
        oc.marcar_etapa(pool, conta, o["id"], o["etapas"][0]["id"], ze)
    ev = oc.recentes(pool, conta)[0]
    assert ev["tipo"] == "etapa" and ev["quem"] == "Seu Zé"
    outro = _membro(pool, conta, nome="Outro mestre")
    with pytest.raises(ValueError, match="você mesmo"):
        oc.desfazer(pool, conta, ev["id"], membro_id=outro)             # mestre não desfaz o alheio
    assert "Desfeito" in oc.desfazer(pool, conta, ev["id"], membro_id=ze)
    assert ob.obter_obra(pool, conta, o["id"])["pct"] == 0
    assert oc.recentes(pool, conta)[0]["desfeito"]
    with pytest.raises(ValueError, match="já foi desfeito"):
        oc.desfazer(pool, conta, ev["id"])


def test_material_aponta_e_desfaz_sem_sobrar_movimento(pool, conta):
    ze = _membro(pool, conta)
    o = _obra(pool, conta, "Casa M", mestre=ze)
    r = oc.material(pool, conta, o["id"], acao="chegou", material="Cimento CP-II 50 kg",
                    quantidade="60", unidade="sc", membro_id=ze)
    assert "60 sacos" in r["frase"]
    r = oc.material(pool, conta, o["id"], acao="usei", material="cimento", quantidade="15,5",
                    membro_id=ze)
    p = omat.achar_produto(pool, conta, "cimento")
    assert omat.saldo_local(pool, conta, p["id"], o["id"]) == Decimal("44.5")
    ev = oc.recentes(pool, conta)[0]
    oc.desfazer(pool, conta, ev["id"], membro_id=ze)
    assert omat.saldo_local(pool, conta, p["id"], o["id"]) == Decimal(60)
    with pytest.raises(ValueError, match="Não conheço"):
        oc.material(pool, conta, o["id"], acao="usei", material="granito", quantidade=1, membro_id=ze)


def test_foto_do_campo(pool, conta):
    ze = _membro(pool, conta)
    o = _obra(pool, conta, "Casa F", mestre=ze)
    r = oc.foto(pool, conta, o["id"], b"\xff\xd8\xff fake", "image/jpeg", etapa="alvenaria",
                membro_id=ze, subir=lambda *a: None)
    assert "Foto da etapa alvenaria" in r["frase"]
    with pool.connection() as c:
        assert c.execute("select origem from obra_fotos where obra_id=%s", (o["id"],)).fetchone()[0] == "campo"


# ── o app ─────────────────────────────────────────────────────────────────
def _app(pool, conta, monkeypatch, papel="mestre", membro_id=None, nicho="construcao"):
    for mod in (ao, po):
        monkeypatch.setattr(mod, "get_pool", lambda: pool)
    linha = [None] * 17
    linha[0] = conta
    for mod in (ao, po):
        monkeypatch.setattr(mod, "conta_logada", lambda request: tuple(linha))
        monkeypatch.setattr(mod, "nicho_da_conta", lambda c: nicho)
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="t")
    app.include_router(ao.router)
    app.include_router(po.router)

    @app.post("/_entrar")
    async def _entrar(request: Request):
        request.session["papel"] = papel
        if membro_id:
            request.session["membro_id"] = membro_id
        return {"ok": True}

    c = TestClient(app, follow_redirects=False)
    c.post("/_entrar", json={})
    return c


def test_o_app_nao_mostra_dinheiro(pool, conta, monkeypatch):
    ze = _membro(pool, conta)
    g = og.criar_grupo(pool, conta, "Quadra D")
    o = _obra(pool, conta, "Casa D", mestre=ze)
    og.definir(pool, conta, o["id"], g["id"], "4")
    c = _app(pool, conta, monkeypatch, membro_id=ze)
    for url in ("/obra", f"/obra/{o['id']}", f"/obra/quadra/{g['id']}"):
        r = c.get(url)
        assert r.status_code == 200, url
        assert "R$" not in r.text and "previsto" not in r.text.lower(), url
        assert "paga" not in r.text.lower(), url


def test_app_fluxo_do_mestre(pool, conta, monkeypatch):
    ze = _membro(pool, conta)
    minha = _obra(pool, conta, "Casa Minha", mestre=ze)
    alheia = _obra(pool, conta, "Casa Alheia")
    c = _app(pool, conta, monkeypatch, membro_id=ze)
    inicio = c.get("/obra").text
    assert "Casa Minha" in inicio and "Casa Alheia" not in inicio
    r = c.get(f"/obra/{alheia['id']}")
    assert r.status_code == 303 and "erro=" in r.headers["location"]
    r = c.post(f"/obra/{alheia['id']}/etapa", data={"etapa_id": str(alheia["etapas"][0]["id"])})
    assert "erro=" in r.headers["location"] and ob.obter_obra(pool, conta, alheia["id"])["pct"] == 0
    r = c.post(f"/obra/{minha['id']}/etapa", data={"etapa_id": str(minha["etapas"][0]["id"])})
    assert "ok=" in r.headers["location"]
    pagina = c.get(f"/obra/{minha['id']}").text
    assert "📷" in pagina and "✅" in pagina and "🧱" in pagina and "desfazer" in pagina
    ev = oc.recentes(pool, conta)[0]
    r = c.post(f"/obra/desfazer/{ev['id']}", data={"volta": f"/obra/{minha['id']}"})
    assert "ok=" in r.headers["location"] and ob.obter_obra(pool, conta, minha["id"])["pct"] == 0
    # o que instala o app: as rotas fixas não caem na rota da obra (:int)
    assert c.get("/obra/manifest.webmanifest").status_code == 200
    assert c.get("/obra/sw.js").status_code == 200 and c.get("/obra/icon.svg").status_code == 200


def test_app_barra_quem_nao_e_do_campo_ou_do_nicho(pool, conta, monkeypatch):
    c = _app(pool, conta, monkeypatch, papel="vendedor", membro_id=_membro(pool, conta, "vendedor", "V"))
    r = c.get("/obra")
    assert r.status_code == 303 and r.headers["location"] == eq.destino_barrado("vendedor")
    c = _app(pool, conta, monkeypatch, papel="dono", nicho="clinica")
    assert c.get("/obra").headers["location"] == "/painel"


# ── o painel do dono ──────────────────────────────────────────────────────
def test_painel_escolhe_o_mestre_e_ve_do_campo(pool, conta, monkeypatch):
    ze = _membro(pool, conta)
    o = _obra(pool, conta, "Casa P")
    c = _app(pool, conta, monkeypatch, papel="dono")
    ficha = c.get(f"/painel/obras/{o['id']}").text
    assert "Mestre de obras" in ficha and "Seu Zé" in ficha
    r = c.post(f"/painel/obras/{o['id']}/editar",
               data={"nome": "Casa P", "tipo": "casa", "status": "em_obra", "mestre_id": str(ze)})
    assert "erro" not in r.headers["location"]
    assert oc.mestre_da_obra(pool, conta, o["id"]) == ze
    oc.marcar_etapa(pool, conta, o["id"], o["etapas"][0]["id"], ze)
    lista = c.get("/painel/obras").text
    assert "Do campo" in lista and "Seu Zé" in lista and "Fundação pronta" in lista
    ev = oc.recentes(pool, conta)[0]
    r = c.post(f"/painel/obras/campo/{ev['id']}/desfazer")
    assert r.status_code == 303 and oc.recentes(pool, conta)[0]["desfeito"]


def test_hora_do_campo_e_de_brasilia(pool, conta):
    # o banco fala UTC; a tela, Brasília (regra 7 do CLAUDE.md)
    ze = _membro(pool, conta)
    o = _obra(pool, conta, "Casa H", mestre=ze)
    oc.marcar_etapa(pool, conta, o["id"], o["etapas"][0]["id"], ze)
    quando = oc.recentes(pool, conta)[0]["quando"]
    assert quando.utcoffset() == timedelta(hours=-3)
