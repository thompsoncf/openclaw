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


# ── as correções da revisão do PR (cada uma com o cenário que quebrava) ──────
def test_apagar_a_nota_leva_o_material_e_relancar_nao_dobra(pool, conta):
    o = _obra(pool, conta, "Casa R1")
    lid = _lanc(pool, conta, centro=o["centro_custo_id"])
    _itens(pool, lid, [("CIMENTO CP II 50KG", 60, "sc")])
    omat.absorver_lancamento(pool, conta, lid)
    p = omat.achar_produto(pool, conta, "cimento cp ii")
    omat.mover(pool, conta, acao="usei", produto_id=p["id"], quantidade=5, obra_id=o["id"])
    assert LivroCaixa(pool, conta).apagar_lancamento(lid)      # "apaga e lança de novo"
    lid2 = _lanc(pool, conta, centro=o["centro_custo_id"])
    _itens(pool, lid2, [("CIMENTO CP II 50KG", 60, "sc")])
    omat.absorver_lancamento(pool, conta, lid2)
    # 60 da nota nova − 5 usados; a nota apagada não deixou 60 órfãos
    assert omat.saldo_local(pool, conta, p["id"], o["id"]) == Decimal(55)


def test_cupom_pessoal_e_de_mercado_nao_viram_material(pool, conta):
    o = _obra(pool, conta, "Casa R2")
    with pool.connection() as c:
        pessoal = c.execute("""insert into lancamentos (conta_id, tipo, valor_centavos, categoria,
                                   descricao, data, natureza)
                               values (%s,'despesa',5000,'Insumos','mercado de casa',%s,'pessoal')
                               returning id""", (conta, HOJE)).fetchone()[0]
        mercado = c.execute("""insert into lancamentos (conta_id, tipo, valor_centavos, categoria,
                                   descricao, data, natureza, centro_custo_id)
                               values (%s,'despesa',5000,'Mercado','café da equipe',%s,'empresa',%s)
                               returning id""", (conta, HOJE, o["centro_custo_id"])).fetchone()[0]
        c.commit()
    _itens(pool, pessoal, [("ARROZ 5KG", 2, "un")])
    _itens(pool, mercado, [("CAFE 500G", 3, "un")])
    assert omat.absorver_lancamento(pool, conta, pessoal) == ""
    assert omat.absorver_lancamento(pool, conta, mercado) == ""
    assert not omat.quadro_da_obra(pool, conta, o["id"])
    assert not [r for r in omat.deposito(pool, conta) if r["nome"] in ("ARROZ 5KG", "CAFE 500G")]


def test_dividir_devolve_o_material_ao_deposito(pool, conta):
    a, b = _obra(pool, conta, "Casa R3a"), _obra(pool, conta, "Casa R3b")
    lid = _lanc(pool, conta, centro=a["centro_custo_id"])
    _itens(pool, lid, [("BRITA 1", 4, "m3")])
    omat.absorver_lancamento(pool, conta, lid)
    ob.dividir(pool, conta, lid, [a["id"], b["id"]])
    assert not [r for r in omat.quadro_da_obra(pool, conta, a["id"]) if r["saldo"] != 0]
    assert any(r["nome"] == "BRITA 1" and r["saldo"] == Decimal(4)
               for r in omat.deposito(pool, conta))


def test_realocar_so_leva_o_que_esta_livre(pool, conta):
    a, b = _obra(pool, conta, "Casa R4a"), _obra(pool, conta, "Casa R4b")
    lid = _lanc(pool, conta, centro=a["centro_custo_id"])
    _itens(pool, lid, [("TELHA CERAMICA", 60, "un")])
    omat.absorver_lancamento(pool, conta, lid)
    p = omat.achar_produto(pool, conta, "telha")
    omat.mover(pool, conta, acao="usei", produto_id=p["id"], quantidade=10, obra_id=a["id"])
    ob.por_na_obra(pool, conta, lid, b["id"])        # a nota era da outra casa
    # os 10 usados ficam usados na A (sem saldo negativo); os 50 livres vão pra B
    assert omat.saldo_local(pool, conta, p["id"], a["id"]) == Decimal(0)
    assert omat.saldo_local(pool, conta, p["id"], b["id"]) == Decimal(50)


def test_nota_nao_junta_material_diferente_nem_unidade_diferente(pool, conta):
    o = _obra(pool, conta, "Casa R5")
    lid = _lanc(pool, conta, centro=o["centro_custo_id"])
    _itens(pool, lid, [("CIMENTO", 10, "sc"), ("COLA CIMENTO PVC 75G", 2, "un"),
                       ("AREIA", 3, "sc"), ("AREIA", 2, "m3")])
    omat.absorver_lancamento(pool, conta, lid)
    nomes = sorted((r["nome"], r["unidade"]) for r in omat.quadro_da_obra(pool, conta, o["id"]))
    assert nomes == [("AREIA", "m³"), ("AREIA", "saco"), ("CIMENTO", "saco"),
                     ("COLA CIMENTO PVC 75G", "unidade")]
    # o produto do catálogo de VENDA da mesma conta nunca recebe nota de obra
    with pool.connection() as c:
        venda = c.execute("""insert into catalogo_produtos (fornecedor_id, nome, unidade, categoria)
                             values (%s,'Tijolo','milheiro','construcao') returning id""",
                          (conta,)).fetchone()[0]
        c.commit()
    lid2 = _lanc(pool, conta, centro=o["centro_custo_id"])
    _itens(pool, lid2, [("Tijolo", 1, "milheiro")])
    omat.absorver_lancamento(pool, conta, lid2)
    with pool.connection() as c:
        assert c.execute("select count(*) from estoque_mov where produto_id=%s",
                         (venda,)).fetchone()[0] == 0


def test_material_ambiguo_pergunta_e_nao_grava(pool, conta):
    o = _obra(pool, conta, "Casa R6")
    lid = _lanc(pool, conta, centro=o["centro_custo_id"])
    _itens(pool, lid, [("CIMENTO CP II 50KG", 10, "sc"), ("CIMENTO BRANCO 1KG", 4, "un")])
    omat.absorver_lancamento(pool, conta, lid)
    r = _fs(pool, conta)["apontar_material"].executar(
        {"acao": "usei", "material": "cimento", "quantidade": 2, "obra": "Casa R6"})
    assert "Qual deles?" in r and "CIMENTO BRANCO 1KG" in r and "Não registrei" in r
    with pool.connection() as c:
        assert c.execute("select count(*) from estoque_mov where fornecedor_id=%s and tipo='saida'",
                         (conta,)).fetchone()[0] == 0


def test_casa_ambigua_nao_cai_na_quadra_e_quadra_pula_as_prontas(pool, conta):
    g = og.criar_grupo(pool, conta, "Quadra 5")
    casas = []
    for n in (1, 2, 3):
        o = _obra(pool, conta, f"Q5 casa {n}")
        og.definir(pool, conta, o["id"], g["id"], str(n))
        with pool.connection() as c:
            c.execute("update obras set inicio_em=%s where id=%s", (HOJE - timedelta(days=9), o["id"]))
            c.commit()
        casas.append(o)
    with pool.connection() as c:
        c.execute("update obras set status='entregue' where id=%s", (casas[2]["id"],))
        c.commit()
    _obra(pool, conta, "Casa 5 da Rua A")
    _obra(pool, conta, "Casa 5 da Rua B")
    fs = _fs(pool, conta)
    fs["apontar_material"].executar({"acao": "chegou", "material": "Bloco de concreto",
                                     "quantidade": 100, "unidade": "un"})
    # "casa 5" é ambígua: pergunta, e NÃO espalha pela Quadra 5
    r = fs["apontar_material"].executar({"acao": "levei", "material": "bloco",
                                         "quantidade": 20, "obra": "casa 5"})
    assert "Não achei a obra" in r and "Apontei" not in r
    # a quadra dita divide só entre as casas em obra (a entregue fica de fora)
    r = fs["apontar_material"].executar({"acao": "levei", "material": "bloco",
                                         "quantidade": 20, "obra": "quadra 5"})
    assert "2 casas" in r and "Q5 casa 3" not in r


def test_gancho_completa_absorcao_nos_caminhos_ja_salvos(pool, conta):
    o = _obra(pool, conta, "Casa R8")
    livro = LivroCaixa(pool, conta)
    base = {f.nome: f for f in construir_ferramentas(livro)}
    lid = _lanc(pool, conta, centro=o["centro_custo_id"])
    item = {"descricao": "Ferro 8mm", "quantidade": 30, "valor_unitario": 40.0,
            "valor_total": 1200.0, "unidade": "barra"}
    base["registrar_itens_cupom"].executar({"lancamento_id": lid, "itens": [item]})
    assert not omat.quadro_da_obra(pool, conta, o["id"])        # sem gancho ainda
    construir_ferramentas_obras(pool, conta, livro=livro)       # agora liga
    r = base["registrar_itens_cupom"].executar({"lancamento_id": lid, "itens": [item]})
    assert "tem itens salvos" in r and "🧱" in r                 # o -1 completa a absorção
    assert omat.quadro_da_obra(pool, conta, o["id"])[0]["entrou"] == Decimal(30)


# ── os ajustes da demonstração de 02/10 (cada um com o que a tela mostrou) ──
def _quadra_com_cimento(pool, conta, nome, casas):
    """casas: [(etapas feitas, sacos comprados, sacos usados)] — tudo na mesma
    quadra, com 4 etapas iguais em cada casa (25% cada)."""
    g = og.criar_grupo(pool, conta, nome)
    out = []
    for n, (feitas, compra, uso) in enumerate(casas, start=1):
        o = _obra(pool, conta, f"{nome} casa {n}")
        ob.salvar_etapas(pool, conta, o["id"], [(None, f"Etapa {i}", 25) for i in range(4)])
        o = ob.obter_obra(pool, conta, o["id"])
        for e in o["etapas"][:feitas]:
            ob.marcar_etapa(pool, conta, o["id"], e["id"], concluida=True)
        og.definir(pool, conta, o["id"], g["id"], str(n))
        lid = _lanc(pool, conta, centro=o["centro_custo_id"])
        _itens(pool, lid, [("CIMENTO CP II 50KG", compra, "sc")])
        omat.absorver_lancamento(pool, conta, lid)
        if uso:
            p = omat.achar_produto(pool, conta, "cimento cp ii")
            omat.mover(pool, conta, acao="usei", produto_id=p["id"], quantidade=uso,
                       obra_id=o["id"])
        out.append(ob.obter_obra(pool, conta, o["id"]))
    return out


def test_irmas_adiantadas_nao_escondem_a_casa_gastona(pool, conta):
    # a casa 3 (50%) comprou 50; a irmã na MESMA altura comprou 39. As duas
    # adiantadas (100% e 75%) compraram muito mais por estarem adiantadas — e
    # antes puxavam a média pra cima e escondiam a gastona.
    c1, c2, c3, c4 = _quadra_com_cimento(pool, conta, "Quadra Alt",
                                         [(4, 70, 0), (3, 60, 0), (2, 50, 0), (2, 39, 0)])
    alerta = omat.alerta_irmas(pool, conta, c3)
    assert "acima das irmãs na mesma altura" in alerta
    assert "comprou 50 sacos" in alerta and "a irmã comprou 39" in alerta


def test_irmas_comparam_na_mesma_regua(pool, conta):
    # a casa 1 aponta uso (40 usados de 50 comprados); a irmã não aponta (só a
    # compra de 45). Uso contra compra acusaria quem aponta: compara compra com
    # compra — 50 contra 45 não é desvio.
    c1, c2 = _quadra_com_cimento(pool, conta, "Quadra Régua", [(2, 50, 40), (2, 45, 0)])
    assert omat.alerta_irmas(pool, conta, c1) == ""
    # as duas apontando: aí sim, uso contra uso
    d1, d2 = _quadra_com_cimento(pool, conta, "Quadra Uso", [(2, 60, 55), (2, 60, 40)])
    assert "usou 55 sacos" in omat.alerta_irmas(pool, conta, d1)


def test_mapa_poe_o_furo_antes_e_esconde_o_saldo_zerado(pool, conta):
    c1, c2 = _quadra_com_cimento(pool, conta, "Quadra Furo", [(2, 10, 14), (2, 10, 10)])
    m = om.criar(pool, conta, "Área Furo")
    om.salvar_lotes(pool, conta, m["id"], [
        {"rotulo": "1", "x": 0, "y": 0, "larg": 70, "alt": 100, "situacao": "meu", "obra_id": c1["id"]},
        {"rotulo": "2", "x": 80, "y": 0, "larg": 70, "alt": 100, "situacao": "meu", "obra_id": c2["id"]}])
    v = {l["rotulo"]: l for l in om.vista(pool, conta, m["id"])["lotes"]}
    assert "uso maior que entrada" in v["1"]["mat_alerta"]       # o furo, não as irmãs
    assert "-4" not in v["1"]["mat"] and v["1"]["mat"] == ""      # nada de saldo negativo na linha
    assert v["2"]["mat"] == ""                                     # nem "0 sacos"


def test_deposito_mostra_o_minimo_logo_depois_do_saldo(pool, conta, monkeypatch):
    with pool.connection() as c:
        pid, _, _ = omat._achar_ou_criar(c, conta, "Argamassa AC-II", "sc")
        c.commit()
    omat.mover(pool, conta, acao="chegou", produto_id=pid, quantidade=8)
    html = _painel(pool, conta, monkeypatch).get("/painel/obras").text
    cab = html[html.index("<th>Material</th>"):]
    assert cab.index("No depósito") < cab.index("Mínimo") < cab.index("Entrou")
