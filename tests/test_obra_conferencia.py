"""A conferência da nota na entrada do CD (finance/obra_conferencia.py; PR 3a do
desenho docs/mockups/obras_cd_almoxarifado.html, aprovado em 03/10/2026).

`test_o_cd_fica_com_o_que_chegou_e_a_nota_leva_isso_junto` é o contrato: a
conferência corrige a própria entrada — e, se a nota mudar de obra depois, vai
junto o que chegou, não o que a nota dizia.
"""
import os
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool
from starlette.middleware.sessions import SessionMiddleware

from db.conexao import init_schema
from finance import obra_conferencia as conf
from finance import obra_material as omat
from finance import obras as ob
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
# as migrações com prefixo de data (db/nova_migracao.py): acha pelo nome
for _nome in ("obra_ferramentas", "obra_conferencia_nota"):
    _MIGRACOES += [p.name for p in sorted(_BASE.glob(f"*_{_nome}.sql"))]


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_obra_conferencia_test"
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


def _nota(pool, conta, itens, fornecedor="Constrular", centro=None):
    """Uma nota de material lida pelo leitor (itens) e absorvida — no CD sem obra."""
    with pool.connection() as c:
        lid = c.execute("""insert into lancamentos (conta_id, tipo, valor_centavos, categoria,
                               descricao, data, natureza, centro_custo_id)
                           values (%s,'despesa',100000,'Insumos',%s,%s,'empresa',%s) returning id""",
                        (conta, fornecedor, date.today(), centro)).fetchone()[0]
        for desc, q, un in itens:
            c.execute("""insert into itens_lancamento (lancamento_id, descricao, quantidade,
                             valor_unitario_centavos, valor_total_centavos, unidade)
                         values (%s,%s,%s,3290,0,%s)""", (lid, desc, q, un))
        c.commit()
    omat.absorver_lancamento(pool, conta, lid)
    return lid


def _cd(pool, conta, nome):
    p = omat.achar_produto(pool, conta, nome)
    return omat.saldo_local(pool, conta, p["id"], None)


# ── conferir ──────────────────────────────────────────────────────────────
def test_bateu_tira_da_lista_e_nao_mexe_no_estoque(pool, conta):
    lid = _nota(pool, conta, [("CIMENTO CP II 50KG", 60, "sc"), ("AREIA MEDIA", 6, "m3")])
    pend = conf.pendentes(pool, conta)
    assert [n["lancamento_id"] for n in pend] == [lid] and len(pend[0]["itens"]) == 2
    r = conf.conferir(pool, conta, lid)
    assert "bateu" in r["frase"] and not r["divergente"]
    assert conf.pendentes(pool, conta) == [] and _cd(pool, conta, "cimento cp ii") == 60
    assert conf.conferidas(pool, conta)[0]["divergente"] is False
    with pytest.raises(ValueError, match="não está pra conferir"):
        conf.conferir(pool, conta, lid)


def test_o_cd_fica_com_o_que_chegou_e_a_nota_leva_isso_junto(pool, conta):
    lid = _nota(pool, conta, [("CIMENTO CP II 50KG", 60, "sc")])
    mov = conf.pendentes(pool, conta)[0]["itens"][0]["mov_id"]
    r = conf.conferir(pool, conta, lid, chegou={mov: "58"})
    assert r["divergente"] and "chegou 58 de 60" in r["faltas"][0]
    assert _cd(pool, conta, "cimento cp ii") == 58                        # o que chegou
    x = conf.conferidas(pool, conta)[0]
    assert x["divergente"] and "chegou 58 de 60" in x["difs"][0]
    assert conf.divergencias(pool, conta) == [{"fornecedor": "Constrular", "notas": 1, "com_diferenca": 1}]
    # a nota muda de obra depois: vai junto o que CHEGOU
    o = ob.criar_obra(pool, conta, "Casa Conf", "casa")
    ob.por_na_obra(pool, conta, lid, o["id"])
    p = omat.achar_produto(pool, conta, "cimento cp ii")
    assert omat.saldo_local(pool, conta, p["id"], o["id"]) == 58
    assert omat.saldo_local(pool, conta, p["id"], None) == 0


def test_desfazer_volta_a_nota_e_o_estoque(pool, conta):
    lid = _nota(pool, conta, [("TELHA CERAMICA", 400, "un")], fornecedor="Telhas Sul")
    mov = conf.pendentes(pool, conta)[0]["itens"][0]["mov_id"]
    conf.conferir(pool, conta, lid, chegou={mov: "380"})
    assert _cd(pool, conta, "telha") == 380
    x = conf.conferidas(pool, conta)[0]
    assert "desfeita" in conf.desfazer(pool, conta, x["id"])
    assert _cd(pool, conta, "telha") == 400                               # voltou à nota
    assert [n["lancamento_id"] for n in conf.pendentes(pool, conta)] == [lid]
    with pytest.raises(ValueError, match="não encontrada"):
        conf.desfazer(pool, conta, x["id"])


def test_so_entra_nota_do_cd_e_quantidade_invalida_recusa(pool, conta):
    o = ob.criar_obra(pool, conta, "Casa Direta", "casa")
    obra = ob.obter_obra(pool, conta, o["id"])
    _nota(pool, conta, [("BRITA 1", 5, "m3")], centro=obra["centro_custo_id"])  # foi direto pra obra
    assert conf.pendentes(pool, conta) == []
    lid = _nota(pool, conta, [("FERRO 8MM", 30, "barra")])
    mov = conf.pendentes(pool, conta)[0]["itens"][0]["mov_id"]
    with pytest.raises(ValueError, match="negativa"):
        conf.conferir(pool, conta, lid, chegou={mov: "-2"})
    with pytest.raises(ValueError, match="inválida"):
        conf.conferir(pool, conta, lid, chegou={mov: "trinta"})
    assert conf.pendentes(pool, conta)                                    # nada foi gravado


def test_outra_conta_nao_confere_nem_desfaz(pool, conta):
    lid = _nota(pool, conta, [("CAL HIDRATADA", 10, "sc")])
    with pool.connection() as c:
        outra = c.execute("insert into contas (tipo, nome) values ('pj','Outra') returning id").fetchone()[0]
        c.commit()
    with pytest.raises(ValueError):
        conf.conferir(pool, outra, lid)
    conf.conferir(pool, conta, lid)
    with pytest.raises(ValueError):
        conf.desfazer(pool, outra, conf.conferidas(pool, conta)[0]["id"])


# ── a tela ────────────────────────────────────────────────────────────────
def _painel(pool, conta, monkeypatch, papel="almoxarife"):
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


def test_tela_confere_e_avisa(pool, conta, monkeypatch):
    lid = _nota(pool, conta, [("CIMENTO CP II 50KG", 60, "sc")], fornecedor="Constrular")
    c = _painel(pool, conta, monkeypatch)
    assert "falta conferir" in c.get("/painel/obras/deposito").text
    html = c.get("/painel/obras/deposito?aba=entradas").text
    assert "Falta conferir" in html and "Constrular" in html and 'name="chegou" value="60"' in html
    mov = conf.pendentes(pool, conta)[0]["itens"][0]["mov_id"]
    r = c.post(f"/painel/obras/deposito/conferir/{lid}", data={"mov_id": [str(mov)], "chegou": ["58"]})
    assert "ok=" in r.headers["location"] and _cd(pool, conta, "cimento cp ii") == 58
    html = c.get("/painel/obras/deposito?aba=entradas").text
    assert "com diferença" in html and "Fornecedores" in html and "Nenhuma nota pra conferir" in html
    r = c.post(f"/painel/obras/deposito/conferir/{lid}", data={"mov_id": [str(mov)], "chegou": ["58"]})
    assert "erro=" in r.headers["location"]                              # já conferida


# ── os achados da verificação independente do #1001 ──────────────────────
def test_conferir_pra_menos_nao_deixa_o_cd_negativo(pool, conta):
    lid = _nota(pool, conta, [("TIJOLO 8 FUROS", 1000, "un")], fornecedor="Olaria")
    p = omat.achar_produto(pool, conta, "tijolo")
    o = ob.criar_obra(pool, conta, "Casa Tijolo", "casa")
    omat.mover(pool, conta, acao="levei", produto_id=p["id"], quantidade=600, obra_id=o["id"])
    mov = conf.pendentes(pool, conta)[0]["itens"][0]["mov_id"]
    with pytest.raises(ValueError, match="só tem 400"):
        conf.conferir(pool, conta, lid, chegou={mov: "500"})            # tiraria 500 de 400
    assert _cd(pool, conta, "tijolo") == 400 and conf.pendentes(pool, conta)   # nada gravado
    conf.conferir(pool, conta, lid, chegou={mov: "700"})                 # tira 300 dos 400
    assert _cd(pool, conta, "tijolo") == 100


def test_nota_que_ja_foi_pras_obras_nao_confere_pra_menos(pool, conta):
    # 10 dos 60 sacos foram pra Casa X; a nota vai pra Casa Y (os 50 livres, por
    # transferência — a entrada fica no CD). Conferir 58 deixaria o CD em -2.
    lid = _nota(pool, conta, [("CIMENTO CP II 50KG", 60, "sc")])
    p = omat.achar_produto(pool, conta, "cimento cp ii")
    x = ob.criar_obra(pool, conta, "Casa X", "casa")
    y = ob.criar_obra(pool, conta, "Casa Y", "casa")
    omat.mover(pool, conta, acao="levei", produto_id=p["id"], quantidade=10, obra_id=x["id"])
    ob.por_na_obra(pool, conta, lid, y["id"])
    assert omat.saldo_local(pool, conta, p["id"], y["id"]) == 50 and _cd(pool, conta, "cimento cp ii") == 0
    mov = conf.pendentes(pool, conta)[0]["itens"][0]["mov_id"]
    with pytest.raises(ValueError, match="já saiu"):
        conf.conferir(pool, conta, lid, chegou={mov: "58"})
    assert _cd(pool, conta, "cimento cp ii") == 0


def test_nota_em_lotes_o_lote_novo_volta_pra_conferir_na_mesma_conferencia(pool, conta):
    lid = _nota(pool, conta, [("CIMENTO CP II 50KG", 60, "sc")])
    conf.conferir(pool, conta, lid)
    assert conf.pendentes(pool, conta) == []
    with pool.connection() as c:                       # a página 2 da nota, anexada depois
        c.execute("""insert into itens_lancamento (lancamento_id, descricao, quantidade,
                         valor_unitario_centavos, valor_total_centavos, unidade)
                     values (%s,'AREIA MEDIA',6,9000,0,'m3')""", (lid,))
        c.commit()
    omat.absorver_lancamento(pool, conta, lid)
    pend = conf.pendentes(pool, conta)
    assert [n["lancamento_id"] for n in pend] == [lid] and len(pend[0]["itens"]) == 1
    assert "AREIA" in pend[0]["itens"][0]["nome"].upper()
    r = conf.conferir(pool, conta, lid, chegou={pend[0]["itens"][0]["mov_id"]: "5"})
    assert r["divergente"] and _cd(pool, conta, "areia") == 5
    x = conf.conferidas(pool, conta)
    assert len(x) == 1 and x[0]["divergente"] and len(x[0]["difs"]) == 1    # a mesma conferência
    conf.desfazer(pool, conta, x[0]["id"])                                 # os dois lotes voltam
    assert len(conf.pendentes(pool, conta)[0]["itens"]) == 2 and _cd(pool, conta, "areia") == 6


def test_desfazer_recusa_quando_a_nota_ja_foi_pra_obra(pool, conta):
    lid = _nota(pool, conta, [("CIMENTO CP II 50KG", 60, "sc")])
    mov = conf.pendentes(pool, conta)[0]["itens"][0]["mov_id"]
    conf.conferir(pool, conta, lid, chegou={mov: "58"})
    o = ob.criar_obra(pool, conta, "Casa D", "casa")
    ob.por_na_obra(pool, conta, lid, o["id"])                 # a entrada inteira muda de lugar
    x = conf.conferidas(pool, conta)[0]
    assert x["pode_desfazer"] is False
    with pytest.raises(ValueError, match="foi pra uma obra"):
        conf.desfazer(pool, conta, x["id"])
    p = omat.achar_produto(pool, conta, "cimento cp ii")
    assert omat.saldo_local(pool, conta, p["id"], o["id"]) == 58          # a obra intacta


def test_desfazer_nao_deixa_o_cd_negativo(pool, conta):
    lid = _nota(pool, conta, [("CIMENTO CP II 50KG", 60, "sc")])
    mov = conf.pendentes(pool, conta)[0]["itens"][0]["mov_id"]
    conf.conferir(pool, conta, lid, chegou={mov: "65"})                  # veio a mais
    p = omat.achar_produto(pool, conta, "cimento cp ii")
    o = ob.criar_obra(pool, conta, "Casa K", "casa")
    omat.mover(pool, conta, acao="levei", produto_id=p["id"], quantidade=65, obra_id=o["id"])
    with pytest.raises(ValueError, match="já saiu do CD"):
        conf.desfazer(pool, conta, conf.conferidas(pool, conta)[0]["id"])
    assert _cd(pool, conta, "cimento cp ii") == 0


def test_quantidade_como_se_digita_no_brasil(pool, conta):
    assert omat.quantidade_br("1.000") == 1000 and omat.quantidade_br("1,5") == Decimal("1.5")
    assert omat.quantidade_br("1.250,5") == Decimal("1250.5") and omat.quantidade_br("1.5") == Decimal("1.5")
    assert omat.quantidade_br("0.500") == Decimal("0.5") and omat.quantidade_br("10.0004") == 10
    for ruim in ("nan", "snan", "inf", "-inf", "1e12", "99999999999", "", "trinta", "-1",
                 "999999999,9999", "999999999.9996"):
        with pytest.raises(ValueError):
            omat.quantidade_br(ruim)
    lid = _nota(pool, conta, [("TIJOLO 8 FUROS", 1000, "un"), ("CONCRETO USINADO", Decimal("1.125"), "m3")])
    itens = {("tijolo" if "TIJOLO" in i["nome"].upper() else "concreto"): i
             for i in conf.pendentes(pool, conta)[0]["itens"]}
    assert itens["concreto"]["valor_campo"] == "1,125" and itens["tijolo"]["valor_campo"] == "1000"
    r = conf.conferir(pool, conta, lid, chegou={itens["tijolo"]["mov_id"]: "1.000",
                                                itens["concreto"]["mov_id"]: itens["concreto"]["valor_campo"]})
    assert not r["divergente"]                  # "1.000" é mil; o campo como veio bate


def test_tela_quantidade_estranha_vira_aviso_e_nao_erro_500(pool, conta, monkeypatch):
    lid = _nota(pool, conta, [("CIMENTO CP II 50KG", 60, "sc")])
    c = _painel(pool, conta, monkeypatch)
    mov = conf.pendentes(pool, conta)[0]["itens"][0]["mov_id"]
    for ruim in ("nan", "inf", "1e12"):
        r = c.post(f"/painel/obras/deposito/conferir/{lid}", data={"mov_id": [str(mov)], "chegou": [ruim]})
        assert r.status_code == 303 and "erro=" in r.headers["location"]
    assert conf.pendentes(pool, conta)
