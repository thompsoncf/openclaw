"""A aba Obras, de ponta a ponta: formulário → rota → banco → a tela que lê de volta.

Mesma lição do test_remetente_rota: testar a função e o HTML separados deixou,
uma vez, uma tela que mostrava um nome que nunca foi gravado. Aqui se atravessa
a rota.
"""
import os
import re
from datetime import date
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool
from starlette.middleware.sessions import SessionMiddleware

from db.conexao import init_schema
from finance import obras as ob
from web import painel_obras as po

# a ficha da casa lê a venda e os papéis (353), e a assinatura cria título a receber
_MIGRACOES = ("018_chave_nfce_lancamentos.sql", "053_modulo_pj.sql",
              "057_natureza_lancamento.sql", "064_clientes_lojista.sql",
              "066_pessoas_identidade.sql", "067_titulos_cliente.sql",
              "131_pessoa_cnpj.sql", "132_plano_contas_centros_custo.sql",
              "182_clientes_papel.sql",
              "195_titulo_aprovacao.sql", "196_titulo_recorrencia.sql",
              "197_titulo_acrescimo.sql", "317_titulo_classificacao.sql",
              "349_plano_obras.sql", "351_obras.sql", "353_obra_venda_documentos.sql",
              "355_reforma_orcamento.sql")
_BASE = Path(__file__).resolve().parent.parent / "db" / "migracoes"


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_painel_obras_test"
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
    yield p
    p.close()


@pytest.fixture()
def conta(pool):
    with pool.connection() as c:
        cid = c.execute("insert into contas (tipo, nome) values ('pj', 'PX2') returning id"
                        ).fetchone()[0]
        c.commit()
    return cid


def _cliente(pool, conta, monkeypatch, nicho="construcao"):
    monkeypatch.setattr(po, "get_pool", lambda: pool)
    linha = [None] * 17
    linha[0] = conta
    monkeypatch.setattr(po, "conta_logada", lambda request: tuple(linha))
    monkeypatch.setattr(po, "nicho_da_conta", lambda c: nicho)
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="teste-de-sessao")
    app.include_router(po.router)

    @app.post("/_entrar")
    async def _entrar(request: Request):
        request.session["papel"] = (await request.json()).get("papel", "dono")
        return {"ok": True}

    c = TestClient(app, follow_redirects=False)
    return c


def _entrar(c, papel="dono"):
    c.post("/_entrar", json={"papel": papel})


def _lanc(pool, conta, valor, categoria="Compras", descricao="Material - Sampaio"):
    with pool.connection() as c:
        lid = c.execute(
            """insert into lancamentos (conta_id, tipo, valor_centavos, categoria, descricao,
                                        data, natureza)
                    values (%s,'despesa',%s,%s,%s,%s,'empresa') returning id""",
            (conta, valor, categoria, descricao, date.today())).fetchone()[0]
        c.commit()
    return lid


# ── quem entra ────────────────────────────────────────────────────────────
def test_so_abre_pra_construcao(pool, conta, monkeypatch):
    c = _cliente(pool, conta, monkeypatch, nicho="clinica")
    _entrar(c)
    r = c.get("/painel/obras")
    assert r.status_code == 303 and r.headers["location"] == "/painel"


def test_o_vendedor_nao_tem_a_aba(pool, conta, monkeypatch):
    c = _cliente(pool, conta, monkeypatch)
    _entrar(c, "vendedor")
    assert c.get("/painel/obras").status_code == 303


@pytest.mark.parametrize("papel", ["dono", "gestor", "financeiro"])
def test_quem_ve_o_financeiro_ve_as_obras(pool, conta, monkeypatch, papel):
    c = _cliente(pool, conta, monkeypatch)
    _entrar(c, papel)
    assert c.get("/painel/obras").status_code == 200


# ── a lista ───────────────────────────────────────────────────────────────
def test_sem_obra_nenhuma_o_formulario_vem_aberto(pool, conta, monkeypatch):
    c = _cliente(pool, conta, monkeypatch)
    _entrar(c)
    html = c.get("/painel/obras").text
    assert '<details class="ob-box" open><summary>+ Nova obra' in html
    assert "Nenhum gasto de obra sem obra" in html


def test_criar_obra_pela_tela(pool, conta, monkeypatch):
    c = _cliente(pool, conta, monkeypatch)
    _entrar(c)
    r = c.post("/painel/obras/nova", data={"nome": "Casa 1", "tipo": "casa",
                                           "endereco": "Qd 4 · Lt 11", "area_m2": "45",
                                           "custo_previsto": "82.000,00", "valor": "150000"})
    assert r.status_code == 303
    oid = int(re.search(r"/painel/obras/(\d+)$", r.headers["location"]).group(1))
    o = ob.obter_obra(pool, conta, oid)
    assert (o["nome"], o["area_m2"], o["custo_previsto_centavos"], o["valor_centavos"]) == \
        ("Casa 1", 45.0, 8_200_000, 15_000_000)
    ficha = c.get(f"/painel/obras/{oid}").text
    assert "Casa 1" in ficha and "Preliminares e fundação" in ficha and "Qd 4 · Lt 11" in ficha
    assert "Casa 1" in c.get("/painel/obras").text


def test_nome_repetido_volta_com_o_erro(pool, conta, monkeypatch):
    c = _cliente(pool, conta, monkeypatch)
    _entrar(c)
    c.post("/painel/obras/nova", data={"nome": "Casa 1"})
    r = c.post("/painel/obras/nova", data={"nome": "casa 1"})
    assert r.status_code == 303 and "erro=" in r.headers["location"]
    assert "Já existe uma obra" in c.get(r.headers["location"]).text


def test_obra_de_outra_conta_nao_abre(pool, conta, monkeypatch):
    with pool.connection() as cx:
        outra = cx.execute("insert into contas (tipo, nome) values ('pj','Outra') returning id"
                           ).fetchone()[0]
        cx.commit()
    alheia = ob.criar_obra(pool, outra, "Casa X")
    c = _cliente(pool, conta, monkeypatch)
    _entrar(c)
    r = c.get(f"/painel/obras/{alheia['id']}")
    assert r.status_code == 303 and r.headers["location"] == "/painel/obras"


def test_o_que_a_pessoa_escreve_sai_escapado(pool, conta, monkeypatch):
    c = _cliente(pool, conta, monkeypatch)
    _entrar(c)
    ob.criar_obra(pool, conta, "<script>alert(1)</script>")
    _lanc(pool, conta, 1_000, descricao="<img src=x onerror=alert(1)>")
    html = c.get("/painel/obras").text
    assert "<script>alert(1)" not in html and "&lt;script&gt;" in html
    assert "<img src=x" not in html


# ── sem obra ──────────────────────────────────────────────────────────────
def test_por_o_gasto_na_obra_pela_lista(pool, conta, monkeypatch):
    o = ob.criar_obra(pool, conta, "Casa 1")
    lid = _lanc(pool, conta, 176_391)
    c = _cliente(pool, conta, monkeypatch)
    _entrar(c)
    assert "R$ 1.763,91" in c.get("/painel/obras").text
    r = c.post("/painel/obras/lancamento", data={"lancamento_id": lid, "obra_id": o["id"]})
    assert r.status_code == 303
    assert ob.obter_obra(pool, conta, o["id"])["custos"]["total"] == 176_391
    assert ob.sem_obra(pool, conta)["n"] == 0


def test_dividir_pela_lista(pool, conta, monkeypatch):
    casas = [ob.criar_obra(pool, conta, f"Casa {n}") for n in (1, 2)]
    lid = _lanc(pool, conta, 100_000)
    c = _cliente(pool, conta, monkeypatch)
    _entrar(c)
    assert "Dividir" in c.get("/painel/obras").text
    c.post("/painel/obras/lancamento", data={"lancamento_id": lid, "acao": "dividir"})
    for casa in casas:
        assert ob.obter_obra(pool, conta, casa["id"])["custos"]["total"] == 50_000


def test_sem_escolher_a_obra_volta_com_o_erro(pool, conta, monkeypatch):
    ob.criar_obra(pool, conta, "Casa 1")
    lid = _lanc(pool, conta, 1_000)
    c = _cliente(pool, conta, monkeypatch)
    _entrar(c)
    r = c.post("/painel/obras/lancamento", data={"lancamento_id": lid, "obra_id": ""})
    assert "erro=" in r.headers["location"]


# ── a ficha ───────────────────────────────────────────────────────────────
def test_marcar_e_desmarcar_etapa_pela_ficha(pool, conta, monkeypatch):
    o = ob.criar_obra(pool, conta, "Casa 1")
    cobertura = next(e for e in o["etapas"] if e["chave"] == "cobertura")
    c = _cliente(pool, conta, monkeypatch)
    _entrar(c)
    c.post(f"/painel/obras/{o['id']}/etapa", data={"etapa_id": cobertura["id"], "concluida": "1"})
    assert ob.obter_obra(pool, conta, o["id"])["pct"] == 12
    c.post(f"/painel/obras/{o['id']}/etapa", data={"etapa_id": cobertura["id"], "concluida": "0"})
    assert ob.obter_obra(pool, conta, o["id"])["pct"] == 0


def test_editar_as_etapas_pela_ficha(pool, conta, monkeypatch):
    o = ob.criar_obra(pool, conta, "Reforma", "reforma")
    c = _cliente(pool, conta, monkeypatch)
    _entrar(c)
    chaves = [e["chave"] for e in o["etapas"]] + [""]
    nomes = [e["nome"] for e in o["etapas"]] + ["Gesso"]
    pesos = ["10"] * len(o["etapas"]) + ["40"]
    r = c.post(f"/painel/obras/{o['id']}/etapas",
               data={"chave": chaves, "nome": nomes, "peso": pesos, "tirar": ["pintura"]})
    assert r.status_code == 303
    etapas = ob.obter_obra(pool, conta, o["id"])["etapas"]
    assert "pintura" not in [e["chave"] for e in etapas]
    assert etapas[-1]["nome"] == "Gesso" and etapas[-1]["peso"] == 40


def test_arquivar_pela_ficha(pool, conta, monkeypatch):
    o = ob.criar_obra(pool, conta, "Casa 1")
    c = _cliente(pool, conta, monkeypatch)
    _entrar(c)
    c.post(f"/painel/obras/{o['id']}/editar", data={"nome": "Casa 1", "tipo": "casa",
                                                    "status": "arquivada"})
    assert ob.obter_obra(pool, conta, o["id"])["status"] == "arquivada"
    assert "Arquivadas · 1" in c.get("/painel/obras").text


# ── a venda e os papéis (PR 3) ────────────────────────────────────────────
def _casa_pronta(pool, conta, nome="Casa 2"):
    o = ob.criar_obra(pool, conta, nome, "casa")
    for e in o["etapas"]:
        ob.marcar_etapa(pool, conta, o["id"], e["id"])
    return o


def test_a_ficha_da_casa_mostra_o_caminho_e_o_que_trava(pool, conta, monkeypatch):
    o = _casa_pronta(pool, conta)
    c = _cliente(pool, conta, monkeypatch)
    _entrar(c)
    html = c.get(f"/painel/obras/{o['id']}").text
    assert "O caminho do dinheiro" in html and "O que trava agora: <b>Habite-se</b>" in html
    assert "Os papéis da casa" in html and "+ Cadastrar a venda" in html
    assert "trava: habite-se" in c.get("/painel/obras").text


def test_a_reforma_nao_tem_venda_nem_papeis(pool, conta, monkeypatch):
    r = ob.criar_obra(pool, conta, "Reforma", "reforma")
    c = _cliente(pool, conta, monkeypatch)
    _entrar(c)
    html = c.get(f"/painel/obras/{r['id']}").text
    assert "O caminho do dinheiro" not in html and "Os papéis da casa" not in html


def test_marcar_o_papel_pela_ficha(pool, conta, monkeypatch):
    o = _casa_pronta(pool, conta)
    c = _cliente(pool, conta, monkeypatch)
    _entrar(c)
    r = c.post(f"/painel/obras/{o['id']}/documento",
               data={"tipo": "habite_se", "status": "ok", "numero": "77/2026",
                     "emitido_em": "2026-09-20", "vence_em": ""})
    assert r.status_code == 303
    from finance import obra_venda as ov
    d = {x["tipo"]: x for x in ov.documentos(pool, conta, o["id"])}["habite_se"]
    assert (d["status"], d["numero"], str(d["emitido_em"])) == ("ok", "77/2026", "2026-09-20")


def test_cadastrar_a_venda_e_assinar_pela_ficha(pool, conta, monkeypatch):
    o = _casa_pronta(pool, conta)
    c = _cliente(pool, conta, monkeypatch)
    _entrar(c)
    c.post(f"/painel/obras/{o['id']}/venda",
           data={"comprador": "J. Silva", "faixa": "2", "modalidade": "financiada",
                 "valor_venda": "150.000,00", "financiamento": "110000",
                 "subsidio": "25.000,00", "fgts": "10000", "entrada": ""})
    from finance import obra_venda as ov
    v = ov.venda(pool, conta, o["id"])
    assert v["entrada_centavos"] == 500_000 and v["repasse_centavos"] == 14_500_000
    c.post(f"/painel/obras/{o['id']}/venda/passo", data={"situacao": "assinatura", "data": ""})
    v = ov.venda(pool, conta, o["id"])
    assert v["situacao"] == "assinatura" and v["titulo_entrada_id"] and v["titulo_repasse_id"]
    html = c.get(f"/painel/obras/{o['id']}").text
    assert "Contrato assinado" in html and "R$ 145.000,00" in html
