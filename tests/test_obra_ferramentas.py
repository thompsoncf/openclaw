"""As ferramentas do CD (finance/obra_ferramentas.py; PR 2 do desenho
docs/mockups/obras_cd_almoxarifado.html, aprovado em 03/10/2026).

`test_ferramenta_nunca_esta_em_dois_lugares` é a trava que importa: mandar pra
outra obra fecha a saída anterior — e o banco recusa duas saídas abertas.
"""
import os
from datetime import date, timedelta
from pathlib import Path

import psycopg
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool
from starlette.middleware.sessions import SessionMiddleware

from db.conexao import init_schema
from finance import obra_campo as oc
from finance import obra_ferramentas as fer
from finance import obra_material as omat
from finance import obras as ob
from web import app_obra as ao
from web import painel_deposito as pd

_BASE = Path(__file__).resolve().parent.parent / "db" / "migracoes"
_MIGRACOES = ["016_unidade_medida.sql", "018_chave_nfce_lancamentos.sql", "019_codigo_gtin.sql",
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
              "371_obra_etapa_pagamentos.sql", "478_obra_quadras.sql", "484_obra_material.sql",
              "550_obra_mestre_e_campo.sql", "670_obra_pedidos_cd.sql"]
# a migração das ferramentas tem prefixo de data (db/nova_migracao.py): acha pelo nome
_MIGRACOES += [p.name for p in sorted(_BASE.glob("*_obra_ferramentas.sql"))]


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_obra_ferramentas_test"
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


def _obra(pool, conta, nome, mestre=None):
    o = ob.criar_obra(pool, conta, nome, "casa")
    if mestre:
        oc.definir_mestre(pool, conta, o["id"], mestre)
    return ob.obter_obra(pool, conta, o["id"])


def _pronta(pool, obra_id):
    with pool.connection() as c:
        c.execute("update obras set status='pronta' where id=%s", (obra_id,))
        c.commit()


def _por_codigo(pool, conta, cod):
    return next(f for f in fer.listar(pool, conta) if f["codigo"] == cod)


# ── o cadastro ────────────────────────────────────────────────────────────
def test_cadastro_da_codigo_a_cada_unidade_e_nao_vira_material(pool, conta):
    assert fer.cadastrar(pool, conta, "Betoneira 400 L") == ["FER-01"]
    assert fer.cadastrar(pool, conta, "Carrinho de mão", 3) == ["FER-02", "FER-03", "FER-04"]
    assert fer.cadastrar(pool, conta, "betoneira 400 l", 1) == ["FER-05"]     # o mesmo tipo
    with pool.connection() as c:
        tipos = c.execute("select count(*) from catalogo_produtos where fornecedor_id=%s "
                          "and categoria='ferramenta'", (conta,)).fetchone()[0]
    assert tipos == 2
    assert omat.achar_produto(pool, conta, "betoneira") is None           # não é material
    with pytest.raises(ValueError, match="nome"):
        fer.cadastrar(pool, conta, "  ")
    with pytest.raises(ValueError, match="1 a 50"):
        fer.cadastrar(pool, conta, "Andaime", 60)


# ── sair, voltar, trocar de obra, dar baixa ───────────────────────────────
def test_ferramenta_nunca_esta_em_dois_lugares(pool, conta):
    cod = fer.cadastrar(pool, conta, "Martelete")[0]
    f = _por_codigo(pool, conta, cod)
    a, b = _obra(pool, conta, "Casa A"), _obra(pool, conta, "Casa B")
    assert "foi pra Casa A, com Bruno" in fer.emprestar(pool, conta, f["id"], a["id"], com_quem="Bruno")
    with pytest.raises(ValueError, match="já está na Casa A"):
        fer.emprestar(pool, conta, f["id"], a["id"])
    fer.emprestar(pool, conta, f["id"], b["id"])                         # direto pra outra casa
    f = _por_codigo(pool, conta, cod)
    assert f["obra"] == "Casa B" and f["fora"]
    with pool.connection() as c:
        assert c.execute("select count(*) from obra_ferramenta_movs where ferramenta_id=%s "
                         "and voltou_em is null", (f["id"],)).fetchone()[0] == 1
        with pytest.raises(psycopg.errors.UniqueViolation):              # o banco também recusa
            c.execute("insert into obra_ferramenta_movs (conta_id, ferramenta_id, obra_id) "
                      "values (%s,%s,%s)", (conta, f["id"], a["id"]))
    assert "voltou pro CD" in fer.devolver(pool, conta, f["id"])
    assert not _por_codigo(pool, conta, cod)["fora"]
    with pytest.raises(ValueError, match="já está no CD"):
        fer.devolver(pool, conta, f["id"])


def test_baixa_tira_da_lista_e_pede_motivo(pool, conta):
    cod = fer.cadastrar(pool, conta, "Serra mármore")[0]
    f = _por_codigo(pool, conta, cod)
    fer.emprestar(pool, conta, f["id"], _obra(pool, conta, "Casa S")["id"])
    with pytest.raises(ValueError, match="motivo"):
        fer.baixar(pool, conta, f["id"], " ")
    assert "baixada: quebrou o disco" in fer.baixar(pool, conta, f["id"], "quebrou o disco")
    assert not [x for x in fer.listar(pool, conta) if x["codigo"] == cod]
    with pool.connection() as c:
        assert c.execute("select count(*) from obra_ferramenta_movs where ferramenta_id=%s "
                         "and voltou_em is null", (f["id"],)).fetchone()[0] == 0


# ── os avisos ─────────────────────────────────────────────────────────────
def test_avisos_obra_pronta_e_fora_ha_mais_de_7_dias(pool, conta):
    c1, c2 = fer.cadastrar(pool, conta, "Andaime (10 peças)", 2)
    a, b = _obra(pool, conta, "Casa Pronta"), _obra(pool, conta, "Casa Longe")
    f1, f2 = _por_codigo(pool, conta, c1), _por_codigo(pool, conta, c2)
    fer.emprestar(pool, conta, f1["id"], a["id"])
    fer.emprestar(pool, conta, f2["id"], b["id"])
    assert not fer.resumo(fer.listar(pool, conta))["alertas"]           # recém saídas, obra andando
    _pronta(pool, a["id"])
    with pool.connection() as c:
        c.execute("update obra_ferramenta_movs set saiu_em = now() - interval '9 days' "
                  "where ferramenta_id=%s", (f2["id"],))
        c.commit()
    lista = {f["codigo"]: f for f in fer.listar(pool, conta)}
    assert lista[c1]["obra_pronta"] and "recolher" in lista[c1]["alerta"]
    assert lista[c2]["alerta"] == "fora há 9 dias — confira"
    assert fer.resumo(list(lista.values()))["fora"] == 2


# ── a casa pronta devolve tudo ────────────────────────────────────────────
def test_devolver_tudo_leva_material_e_ferramentas(pool, conta):
    o = _obra(pool, conta, "Casa Tudo")
    cod = fer.cadastrar(pool, conta, "Betoneira 150 L")[0]
    fer.emprestar(pool, conta, _por_codigo(pool, conta, cod)["id"], o["id"])
    with pool.connection() as c:
        pid, _, _ = omat._achar_ou_criar(c, conta, "Cimento CP-II", "sc")
        c.commit()
    omat.mover(pool, conta, acao="chegou", produto_id=pid, quantidade=10, obra_id=o["id"])
    _pronta(pool, o["id"])
    txt = fer.devolver_tudo(pool, conta, o["id"])
    assert "Voltou pro CD" in txt and "Betoneira 150 L" in txt
    assert not _por_codigo(pool, conta, cod)["fora"]
    assert omat.saldo_local(pool, conta, pid, o["id"]) == 0
    with pytest.raises(ValueError, match="nem ferramenta"):
        fer.devolver_tudo(pool, conta, o["id"])


# ── a tela do CD ──────────────────────────────────────────────────────────
def _painel(pool, conta, monkeypatch, papel="dono"):
    monkeypatch.setattr(pd, "get_pool", lambda: pool)
    linha = [None] * 17
    linha[0] = conta
    monkeypatch.setattr(pd, "conta_logada", lambda request: tuple(linha))
    monkeypatch.setattr(pd, "nicho_da_conta", lambda c: "construcao")
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="t")
    app.include_router(pd.router)

    @app.post("/_entrar")
    async def _entrar(request: Request):
        request.session["papel"] = papel
        return {"ok": True}

    c = TestClient(app, follow_redirects=False)
    c.post("/_entrar", json={})
    return c


def test_tela_cadastra_manda_e_recolhe(pool, conta, monkeypatch):
    o = _obra(pool, conta, "Casa Tela")
    c = _painel(pool, conta, monkeypatch, papel="almoxarife")
    assert "Nenhuma ferramenta cadastrada" in c.get("/painel/obras/deposito?aba=ferramentas").text
    r = c.post("/painel/obras/deposito/ferramenta/nova", data={"nome": "Betoneira 400 L", "quantidade": "2"})
    assert "FER-01" in r.headers["location"] or "ok=" in r.headers["location"]
    f = _por_codigo(pool, conta, "FER-01")
    r = c.post(f"/painel/obras/deposito/ferramenta/{f['id']}/emprestar",
               data={"obra_id": str(o["id"]), "com_quem": "Seu Zé"})
    assert "ok=" in r.headers["location"]
    html = c.get("/painel/obras/deposito?aba=ferramentas").text
    assert "Casa Tela" in html and "Seu Zé" in html and "Devolver ao CD" in html
    _pronta(pool, o["id"])
    geral = c.get("/painel/obras/deposito").text
    assert "Ferramentas fora" in geral and "recolher" in geral
    assert "Recolher" in c.get("/painel/obras/deposito?aba=sobras").text
    r = c.post(f"/painel/obras/deposito/ferramenta/{f['id']}/devolver")
    assert "ok=" in r.headers["location"] and not _por_codigo(pool, conta, "FER-01")["fora"]
    r = c.post(f"/painel/obras/deposito/ferramenta/{f['id']}/emprestar", data={"obra_id": ""})
    assert "erro=" in r.headers["location"]


# ── o app do mestre ───────────────────────────────────────────────────────
def _app(pool, conta, monkeypatch, membro_id):
    monkeypatch.setattr(ao, "get_pool", lambda: pool)
    linha = [None] * 17
    linha[0] = conta
    monkeypatch.setattr(ao, "conta_logada", lambda request: tuple(linha))
    monkeypatch.setattr(ao, "nicho_da_conta", lambda c: "construcao")
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="t")
    app.include_router(ao.router)

    @app.post("/_entrar")
    async def _entrar(request: Request):
        request.session["papel"] = "mestre"
        request.session["membro_id"] = membro_id
        request.session["conta_id"] = conta
        return {"ok": True}

    c = TestClient(app, follow_redirects=False)
    c.post("/_entrar", json={})
    return c


def test_mestre_ve_e_devolve_as_ferramentas_da_obra(pool, conta, monkeypatch):
    with pool.connection() as c:
        ze = c.execute("insert into membros (conta_id, nome, papel, ativo) values (%s,'Seu Zé','mestre',true) "
                       "returning id", (conta,)).fetchone()[0]
        c.commit()
    minha = _obra(pool, conta, "Casa do Zé", mestre=ze)
    outra = _obra(pool, conta, "Casa de Outro")
    c1, c2 = fer.cadastrar(pool, conta, "Furadeira", 2)
    f1, f2 = _por_codigo(pool, conta, c1), _por_codigo(pool, conta, c2)
    fer.emprestar(pool, conta, f1["id"], minha["id"])
    fer.emprestar(pool, conta, f2["id"], outra["id"])
    c = _app(pool, conta, monkeypatch, ze)
    pagina = c.get(f"/obra/{minha['id']}").text
    assert "Ferramentas nesta obra" in pagina and c1 in pagina and c2 not in pagina
    r = c.post(f"/obra/{minha['id']}/ferramenta/{f2['id']}/devolver")       # não é desta obra
    assert "erro=" in r.headers["location"] and _por_codigo(pool, conta, c2)["fora"]
    r = c.post(f"/obra/{minha['id']}/ferramenta/{f1['id']}/devolver")
    assert "ok=" in r.headers["location"] and not _por_codigo(pool, conta, c1)["fora"]
    # casa pronta: o "devolver ao CD" leva as ferramentas junto
    fer.emprestar(pool, conta, f1["id"], minha["id"])
    _pronta(pool, minha["id"])
    pagina = c.get(f"/obra/{minha['id']}").text
    assert "A casa ficou pronta" in pagina and "🔧" in pagina
    c.post(f"/obra/{minha['id']}/devolver")
    assert not _por_codigo(pool, conta, c1)["fora"]


# ── os achados da verificação independente do #997 ──────────────────────────
def test_nome_com_apostrofo_nao_vira_javascript(pool, conta, monkeypatch):
    # "Bomba d'água" quebrava o confirm do "Dar baixa" (e o formulário enviava
    # sem perguntar); um nome feito pra isso viraria código na sessão do dono
    fer.cadastrar(pool, conta, "Bomba d'água")
    fer.cadastrar(pool, conta, "x'+alert(document.domain)+'")
    html = _painel(pool, conta, monkeypatch).get("/painel/obras/deposito?aba=ferramentas").text
    assert "confirm('Dar baixa" not in html                     # o dado não entra no JS
    assert 'data-confirma="Dar baixa em Bomba d&#39;água' in html
    assert "confirm(this.dataset.confirma)" in html
    assert "alert(document.domain)+' (" not in html.split("data-confirma")[0]


def test_clique_duplo_em_mandar_nao_da_500(pool, conta):
    import threading
    cod = fer.cadastrar(pool, conta, "Betoneira dupla")[0]
    f = _por_codigo(pool, conta, cod)
    o = _obra(pool, conta, "Casa Dupla")
    resultados = []

    def mandar():
        try:
            resultados.append(fer.emprestar(pool, conta, f["id"], o["id"]))
        except ValueError as e:                                   # o esperado da segunda
            resultados.append(f"recusado: {e}")
        except Exception as e:  # noqa: BLE001 — o 500 de antes
            resultados.append(f"ERRO {type(e).__name__}")

    ts = [threading.Thread(target=mandar) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not [r for r in resultados if r.startswith("ERRO")], resultados
    assert sum(1 for r in resultados if "foi pra" in r) == 1
    assert sum(1 for r in resultados if "já está na Casa Dupla" in r) == 1


def test_cadastro_ao_mesmo_tempo_vira_recado(pool, conta, monkeypatch):
    fer.cadastrar(pool, conta, "Furadeira de impacto")              # FER-01 já existe
    monkeypatch.setattr(fer, "_proximo_numero", lambda c, conta_id: 1)   # o outro pegou o mesmo
    with pytest.raises(ValueError, match="ao mesmo tempo"):
        fer.cadastrar(pool, conta, "Lixadeira")


def test_devolver_tudo_so_na_casa_pronta(pool, conta):
    o = _obra(pool, conta, "Casa Andando")
    cod = fer.cadastrar(pool, conta, "Prumo")[0]
    fer.emprestar(pool, conta, _por_codigo(pool, conta, cod)["id"], o["id"])
    with pytest.raises(ValueError, match="ainda está em obra"):
        fer.devolver_tudo(pool, conta, o["id"])
    assert _por_codigo(pool, conta, cod)["fora"]                    # nada saiu da obra
