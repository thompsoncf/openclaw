"""O controle de material da obra (finance/obra_material.py, migração 484;
desenho docs/mockups/obras_mapa_3d.html, seção 3).

`test_o_custo_nao_e_tocado` é o contrato central: material é régua de
QUANTIDADE — absorver a nota, usar e transferir nunca cria lançamento nem mexe
em valor. Sem isso, o custo da casa contaria o cimento duas vezes.
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

from db.conexao import init_schema
from finance import obra_grupos as og
from finance import obra_mapas as om
from finance import obra_material as omat
from finance import obras as ob
from finance.livro_caixa import LivroCaixa
from finance.tools import construir_ferramentas
from finance.tools_pj import construir_ferramentas_obras
from web import painel_obras as po
from web import painel_obras_mapa as pom

_MIGRACOES = ("016_unidade_medida.sql", "018_chave_nfce_lancamentos.sql",
              "019_codigo_gtin.sql", "032_catalogo_estoque.sql", "053_modulo_pj.sql",
              "057_natureza_lancamento.sql", "058_dados_empresa.sql",
              "060_unidade_generica.sql", "064_clientes_lojista.sql",
              "066_pessoas_identidade.sql", "067_titulos_cliente.sql",
              "131_pessoa_cnpj.sql", "132_plano_contas_centros_custo.sql",
              "143_plano_contas_locacao_buffet_servicos.sql", "182_clientes_papel.sql",
              "186_plano_aporte_socios.sql", "195_titulo_aprovacao.sql",
              "196_titulo_recorrencia.sql", "197_titulo_acrescimo.sql",
              "317_titulo_classificacao.sql", "325_tipo_despesa.sql",
              "336_plano_fardamentos.sql", "349_plano_obras.sql", "351_obras.sql",
              "353_obra_venda_documentos.sql", "355_reforma_orcamento.sql",
              "365_obras_lembrete_segunda.sql",
              "367_pix_da_empresa.sql", "369_obra_fotos.sql", "371_obra_etapa_pagamentos.sql",
              "478_obra_quadras.sql", "482_obra_mapas.sql", "484_obra_material.sql")
_BASE = Path(__file__).resolve().parent.parent / "db" / "migracoes"
HOJE = date.today()


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_obra_material_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname} with (force)")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=4, open=True, kwargs={"prepare_threshold": None})
    init_schema(p)
    with p.connection() as c:
        # o esqueleto do banco de preços (como em test_holerite): a 016/019 alteram
        # essa tabela, e a coleta de preço do registrar_itens engole erro sozinha
        c.execute("create table if not exists precos_observados "
                  "(id bigserial primary key, item_id bigint)")
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


def _lanc(pool, conta, valor=100_000, centro=None, descricao="Depósito Constrular"):
    with pool.connection() as c:
        lid = c.execute("""insert into lancamentos (conta_id, tipo, valor_centavos, categoria,
                                                    descricao, data, natureza, centro_custo_id)
                           values (%s,'despesa',%s,'Insumos',%s,%s,'empresa',%s) returning id""",
                        (conta, valor, descricao, HOJE, centro)).fetchone()[0]
        c.commit()
    return lid


def _itens(pool, lanc, itens):
    ids = []
    with pool.connection() as c:
        for desc, qtd, un in itens:
            ids.append(c.execute(
                """insert into itens_lancamento (lancamento_id, descricao, quantidade,
                       valor_unitario_centavos, valor_total_centavos, unidade)
                   values (%s,%s,%s, 3290, 0, %s) returning id""",
                (lanc, desc, qtd, un)).fetchone()[0])
        c.commit()
    return ids


def _obra(pool, conta, nome="Casa M1", **kw):
    o = ob.criar_obra(pool, conta, nome, "casa", **kw)
    return ob.obter_obra(pool, conta, o["id"])


# ── a nota vira quantidade ────────────────────────────────────────────────
def test_nota_vira_material_na_obra_sem_duplicar(pool, conta):
    o = _obra(pool, conta)
    lid = _lanc(pool, conta, centro=o["centro_custo_id"])
    _itens(pool, lid, [("CIMENTO CP II 50KG", 20, "sc"), ("FERRO 8MM BARRA 12M", 30, "barra")])
    frase = omat.absorver_lancamento(pool, conta, lid)
    assert "2 materiais" in frase and "Casa M1" in frase
    linhas = {r["nome"]: r for r in omat.quadro_da_obra(pool, conta, o["id"])}
    assert linhas["CIMENTO CP II 50KG"]["entrou"] == Decimal(20)
    assert linhas["FERRO 8MM BARRA 12M"]["entrou"] == Decimal(30)
    assert linhas["CIMENTO CP II 50KG"]["chave"]
    assert linhas["CIMENTO CP II 50KG"]["unidade"] == "saco"
    # reabsorver não duplica; o lote seguinte entra só com o que falta
    assert omat.absorver_lancamento(pool, conta, lid) == ""
    _itens(pool, lid, [("AREIA MEDIA", 6, "m3")])
    assert "1 materiais" in omat.absorver_lancamento(pool, conta, lid)
    assert len(omat.quadro_da_obra(pool, conta, o["id"])) == 3


def test_nota_sem_obra_cai_no_deposito_e_o_botao_leva_junto(pool, conta):
    o = _obra(pool, conta, "Casa M2")
    lid = _lanc(pool, conta)                      # sem centro: ainda sem obra
    _itens(pool, lid, [("TIJOLO 8 FUROS", 4, "milheiro")])
    assert "depósito" in omat.absorver_lancamento(pool, conta, lid)
    assert omat.deposito(pool, conta)[0]["saldo"] == Decimal(4)
    ob.por_na_obra(pool, conta, lid, o["id"])     # o toque no botão
    assert not [r for r in omat.deposito(pool, conta) if r["saldo"] > 0]
    assert omat.quadro_da_obra(pool, conta, o["id"])[0]["entrou"] == Decimal(4)


def test_gancho_apos_itens_no_registrar_itens_cupom(pool, conta):
    o = _obra(pool, conta, "Casa M3")
    livro = LivroCaixa(pool, conta)
    base = {f.nome: f for f in construir_ferramentas(livro)}
    construir_ferramentas_obras(pool, conta, livro=livro)      # liga o gancho
    lid = _lanc(pool, conta, centro=o["centro_custo_id"])
    r = base["registrar_itens_cupom"].executar({
        "lancamento_id": lid,
        "itens": [{"descricao": "Cimento CP-II 50 kg", "quantidade": 60,
                   "valor_unitario": 32.90, "valor_total": 1974.0, "unidade": "sc"}]})
    assert "Salvei 1 itens" in r and "🧱" in r and "Casa M3" in r
    assert omat.quadro_da_obra(pool, conta, o["id"])[0]["entrou"] == Decimal(60)


def test_o_custo_nao_e_tocado(pool, conta):
    o = _obra(pool, conta, "Casa M4")
    lid = _lanc(pool, conta, valor=197_400, centro=o["centro_custo_id"])
    _itens(pool, lid, [("CIMENTO CP II", 60, "sc")])
    with pool.connection() as c:
        antes = c.execute("select count(*), sum(valor_centavos) from lancamentos "
                          "where conta_id=%s", (conta,)).fetchone()
    omat.absorver_lancamento(pool, conta, lid)
    p = omat.achar_produto(pool, conta, "cimento")
    omat.mover(pool, conta, acao="usei", produto_id=p["id"], quantidade=15, obra_id=o["id"])
    with pool.connection() as c:
        depois = c.execute("select count(*), sum(valor_centavos) from lancamentos "
                           "where conta_id=%s", (conta,)).fetchone()
        custos = c.execute("select custo_unit_centavos from estoque_mov "
                           "where fornecedor_id=%s", (conta,)).fetchall()
    assert antes == depois                       # nenhum lançamento novo, nenhum valor mudado
    assert all(r[0] is None for r in custos)     # entrada de obra não carrega custo (CMP em paz)


# ── usei / levei / chegou ─────────────────────────────────────────────────
def test_usei_levei_chegou_e_o_furo(pool, conta):
    o = _obra(pool, conta, "Casa M5")
    with pool.connection() as c:
        pid, _, _ = omat._achar_ou_criar(c, conta, "Cimento CP-II 50 kg", "sc")
        c.commit()
    omat.mover(pool, conta, acao="chegou", produto_id=pid, quantidade=60)
    r = omat.mover(pool, conta, acao="levei", produto_id=pid, quantidade=10, obra_id=o["id"])
    assert r["deposito"] == Decimal(50) and r["na_obra"] == Decimal(10)
    r = omat.mover(pool, conta, acao="usei", produto_id=pid, quantidade=4, obra_id=o["id"])
    assert r["na_obra"] == Decimal(6) and not r["furo"]
    r = omat.mover(pool, conta, acao="usei", produto_id=pid, quantidade=20, obra_id=o["id"])
    assert r["furo"]                             # uso maior que entrada = furo apontado
    linhas = omat.quadro_da_obra(pool, conta, o["id"])
    assert omat.furos(linhas) and "uso maior que entrada" in omat.furos(linhas)[0]
    # as duas pernas da transferência ficam pareadas
    with pool.connection() as c:
        t = c.execute("select count(distinct transf_id), count(*) from estoque_mov "
                      "where fornecedor_id=%s and transf_id is not null", (conta,)).fetchone()
    assert t == (1, 2)


def test_minimo_do_deposito(pool, conta):
    with pool.connection() as c:
        pid, _, _ = omat._achar_ou_criar(c, conta, "Argamassa AC-II", "sc")
        c.commit()
    omat.mover(pool, conta, acao="chegou", produto_id=pid, quantidade=8)
    omat.salvar_minimo(pool, conta, pid, 10)
    dep = {r["produto_id"]: r for r in omat.deposito(pool, conta)}
    assert dep[pid]["abaixo"]
    r = omat.mover(pool, conta, acao="chegou", produto_id=pid, quantidade=20)
    assert not r["abaixo_minimo"]


def test_alerta_compara_com_as_irmas_da_quadra(pool, conta):
    g = og.criar_grupo(pool, conta, "Quadra M")
    casas = []
    for n, sacos in ((1, 48), (2, 39), (3, 40)):
        o = _obra(pool, conta, f"Quadra M casa {n}", area_m2=45)
        og.definir(pool, conta, o["id"], g["id"], str(n))
        lid = _lanc(pool, conta, centro=o["centro_custo_id"])
        _itens(pool, lid, [("CIMENTO CP II 50KG", sacos, "sc")])
        omat.absorver_lancamento(pool, conta, lid)
        casas.append(o)
    alerta = omat.alerta_irmas(pool, conta, ob.obter_obra(pool, conta, casas[0]["id"]))
    assert "acima das irmãs" in alerta and "48 sacos" in alerta and "2 irmãs" in alerta
    assert omat.alerta_irmas(pool, conta, ob.obter_obra(pool, conta, casas[1]["id"])) == ""


# ── as ferramentas do agente ──────────────────────────────────────────────
def _fs(pool, conta):
    return {f.nome: f for f in construir_ferramentas_obras(pool, conta)}


def test_ferramenta_apontar_material(pool, conta):
    o = _obra(pool, conta, "Casa M6")
    fs = _fs(pool, conta)
    r = fs["apontar_material"].executar(
        {"acao": "chegou", "material": "Cimento CP-II 50 kg", "quantidade": 60, "unidade": "sacos"})
    assert "Chegou" in r and "60 sacos" in r and "depósito" in r
    r = fs["apontar_material"].executar(
        {"acao": "levei", "material": "cimento", "quantidade": 10, "obra": "Casa M6"})
    assert "levados do depósito" in r and "50 sacos" in r
    r = fs["apontar_material"].executar(
        {"acao": "usei", "material": "cimento", "quantidade": 4, "obra": "Casa M6"})
    assert "usados em Casa M6" in r and "6 sacos" in r
    assert "Não conheço" in fs["apontar_material"].executar(
        {"acao": "usei", "material": "granito", "quantidade": 1, "obra": "Casa M6"})
    assert "De qual obra" in fs["apontar_material"].executar(
        {"acao": "usei", "material": "cimento", "quantidade": 1})


def test_ferramenta_na_quadra_divide_entre_as_casas(pool, conta):
    g = og.criar_grupo(pool, conta, "Quadra N")
    for n in (1, 2):
        o = _obra(pool, conta, f"Quadra N casa {n}")
        og.definir(pool, conta, o["id"], g["id"], str(n))
        with pool.connection() as c:
            c.execute("update obras set inicio_em=%s where id=%s",
                      (HOJE - timedelta(days=10), o["id"]))
            c.commit()
    fs = _fs(pool, conta)
    fs["apontar_material"].executar(
        {"acao": "chegou", "material": "Brita 1", "quantidade": 10, "unidade": "m3"})
    r = fs["apontar_material"].executar(
        {"acao": "levei", "material": "brita", "quantidade": 10, "obra": "quadra N"})
    assert "2 casas" in r and "Quadra N casa 1 (5 m³)" in r


def test_ferramenta_consultar_material(pool, conta):
    o = _obra(pool, conta, "Casa M7")
    lid = _lanc(pool, conta, centro=o["centro_custo_id"])
    _itens(pool, lid, [("CIMENTO CP II 50KG", 60, "sc")])
    omat.absorver_lancamento(pool, conta, lid)
    fs = _fs(pool, conta)
    r = fs["consultar_material"].executar({"obra": "Casa M7"})
    assert "Material de Casa M7" in r and "entrou 60 sacos" in r and "na obra 60 sacos" in r
    assert "depósito" in fs["consultar_material"].executar({}).lower()


def test_prompt_do_nicho_ensina_o_material(pool, conta):
    from finance.nichos import persona_do_nicho
    p = persona_do_nicho("construcao")
    assert "registrar_itens_cupom" in p and "apontar_material" in p
    assert "CONTROLE DE MATERIAL" in p and "OPCIONAL" in p


# ── o painel e o mapa ─────────────────────────────────────────────────────
def _painel(pool, conta, monkeypatch):
    for mod in (po, pom):
        monkeypatch.setattr(mod, "get_pool", lambda: pool)
    linha = [None] * 17
    linha[0] = conta
    monkeypatch.setattr(po, "conta_logada", lambda request: tuple(linha))
    monkeypatch.setattr(po, "nicho_da_conta", lambda c: "construcao")
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="t")
    app.include_router(pom.router)
    app.include_router(po.router)

    @app.post("/_entrar")
    async def _entrar(request: Request):
        request.session["papel"] = "dono"
        return {"ok": True}

    c = TestClient(app, follow_redirects=False)
    c.post("/_entrar", json={})
    return c


def test_painel_ficha_e_deposito(pool, conta, monkeypatch):
    o = _obra(pool, conta, "Casa M8")
    lid = _lanc(pool, conta, centro=o["centro_custo_id"])
    _itens(pool, lid, [("CIMENTO CP II 50KG", 60, "sc")])
    omat.absorver_lancamento(pool, conta, lid)
    p = omat.achar_produto(pool, conta, "cimento")
    omat.mover(pool, conta, acao="chegou", produto_id=p["id"], quantidade=5)
    c = _painel(pool, conta, monkeypatch)
    ficha = c.get(f"/painel/obras/{o['id']}").text
    assert "Material na obra" in ficha and "60 sacos" in ficha
    lista = c.get("/painel/obras").text
    assert "Depósito de material" in lista and "5 sacos" in lista
    r = c.post("/painel/obras/deposito-minimo",
               data={"produto": str(p["id"]), "minimo": "10"})
    assert r.status_code == 303
    lista = c.get("/painel/obras").text
    assert "abaixo do mínimo" in lista


def test_mapa_perfil_leva_o_material(pool, conta, monkeypatch):
    o = _obra(pool, conta, "Casa M9")
    lid = _lanc(pool, conta, centro=o["centro_custo_id"])
    _itens(pool, lid, [("CIMENTO CP II 50KG", 12, "sc")])
    omat.absorver_lancamento(pool, conta, lid)
    m = om.criar(pool, conta, "Área Mat")
    om.salvar_lotes(pool, conta, m["id"], [{"rotulo": "1", "x": 0, "y": 0, "larg": 70,
                                            "alt": 100, "situacao": "meu", "obra_id": o["id"]}])
    v = om.vista(pool, conta, m["id"])
    assert v["lotes"][0]["mat"] == "12 sacos de CIMENTO CP II 50KG"
    c = _painel(pool, conta, monkeypatch)
    html = c.get(f"/painel/obras/mapa?m={m['id']}").text
    assert "pf-mat" in html and "12 sacos de CIMENTO" in html
