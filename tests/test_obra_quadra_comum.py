"""O PR 2 das obras por quadra: o custo comum pelo m², o mapa e o lembrete agrupado
(finance/obra_grupos.py; desenho docs/mockups/obras_por_quadra.html, seções 4 e 6).

`test_o_custo_comum_entra_pelo_m2_no_custo_cheio` — sem isso a casa parece mais
barata do que é, e a margem e a comparação com o SINAPI mentem.
"""
import os
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool
from starlette.middleware.sessions import SessionMiddleware

from db.conexao import init_schema
from finance import obra_grupos as og
from finance import obras as ob
from finance import obras_lembrete as obl
from finance import tools_pj
from web import painel_obras as po

_MIGRACOES = ("018_chave_nfce_lancamentos.sql", "053_modulo_pj.sql",
              "057_natureza_lancamento.sql", "058_dados_empresa.sql",
              "064_clientes_lojista.sql",
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
              "478_obra_quadras.sql")
_BASE = Path(__file__).resolve().parent.parent / "db" / "migracoes"
HOJE = date.today()


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_obra_quadra_comum_test"
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
        cid = c.execute("insert into contas (tipo, nome, nome_fantasia) values "
                        "('pj', 'Pablo', 'PX2 Empreendimentos') returning id").fetchone()[0]
        c.commit()
    return cid


def _lanc(pool, conta, valor, centro=None, descricao="terraplanagem"):
    with pool.connection() as c:
        lid = c.execute("""insert into lancamentos (conta_id, tipo, valor_centavos, categoria, descricao,
                                                    data, natureza, centro_custo_id)
                           values (%s,'despesa',%s,'Servicos',%s,%s,'empresa',%s) returning id""",
                        (conta, valor, descricao, HOJE, centro)).fetchone()[0]
        c.commit()
    return lid


def _quadra(pool, conta, areas=(90, 45), nome="Quadra 4"):
    g = og.criar_grupo(pool, conta, nome)
    ids = []
    for n, area in enumerate(areas, start=1):
        o = ob.criar_obra(pool, conta, f"{nome} casa {n}", "casa", area_m2=area,
                          custo_previsto_centavos=10_000_000, valor_centavos=15_000_000)
        og.definir(pool, conta, o["id"], g["id"], str(n))
        ids.append(o["id"])
    return g, ids


# ── o centro da quadra ────────────────────────────────────────────────────
def _centro(pool, cid):
    with pool.connection() as c:
        return c.execute("select nome, ativo from centros_custo where id=%s", (cid,)).fetchone()


def test_a_quadra_nasce_com_o_centro_do_custo_comum(pool, conta):
    g = og.criar_grupo(pool, conta, "Quadra 1")
    cid = og.centro_da_quadra(pool, conta, g["id"])
    assert _centro(pool, cid) == ("Quadra 1 · comum", True)
    og.editar_grupo(pool, conta, g["id"], "Quadra 1A")
    assert _centro(pool, cid)[0] == "Quadra 1A · comum"
    og.apagar_grupo(pool, conta, g["id"])
    assert _centro(pool, cid)[1] is False                        # inativo, não apagado


def test_quadra_com_custo_comum_nao_se_apaga(pool, conta):
    g = og.criar_grupo(pool, conta, "Quadra 2")
    _lanc(pool, conta, 100_000, centro=og.centro_da_quadra(pool, conta, g["id"]))
    with pytest.raises(ValueError, match="custo comum lançado"):
        og.apagar_grupo(pool, conta, g["id"])


def test_quadra_antiga_ganha_o_centro_quando_abre_no_painel(pool, conta):
    g = og.criar_grupo(pool, conta, "Quadra 3")
    with pool.connection() as c:
        c.execute("update obra_grupos set centro_custo_id=null where id=%s", (g["id"],))
        c.commit()
    assert og.centro_da_quadra(pool, conta, g["id"]) is None
    with pytest.raises(ValueError, match="Abra a quadra no painel"):
        og.por_na_quadra(pool, conta, _lanc(pool, conta, 100), g["id"])
    assert og.garantir_centro(pool, conta, g["id"]) == og.garantir_centro(pool, conta, g["id"])


# ── o custo comum pelo m² ─────────────────────────────────────────────────
def test_o_custo_comum_entra_pelo_m2_no_custo_cheio(pool, conta):
    g, (grande, pequena) = _quadra(pool, conta)
    og.por_na_quadra(pool, conta, _lanc(pool, conta, 900_001), g["id"])
    _lanc(pool, conta, 4_500_000, centro=ob.obter_obra(pool, conta, pequena)["centro_custo_id"])
    assert og.custo_comum(pool, conta, g["id"]) == 900_001
    assert og.partes_do_comum(og.casas(pool, conta, g["id"]), 900_001) == {grande: 600_001, pequena: 300_000}
    cheia = og.com_comum(pool, conta, ob.obter_obra(pool, conta, pequena))
    assert cheia["custos"]["comum"] == 300_000 and cheia["custos"]["total"] == 4_800_000
    assert cheia["custo_m2"] == 106_667 and cheia["pct_previsto"] == 48
    m = ob.margem(cheia)
    assert m["custo"] == 10_000_000 and m["base"] == "previsto"      # previsto ainda maior que o gasto


def test_sem_area_divide_igual_e_casa_sem_quadra_nao_muda(pool, conta):
    g, (a, b) = _quadra(pool, conta, areas=(90, None), nome="Quadra 5")
    og.por_na_quadra(pool, conta, _lanc(pool, conta, 1_000), g["id"])
    assert og.partes_do_comum(og.casas(pool, conta, g["id"]), 1_000) == {a: 500, b: 500}
    solta = ob.criar_obra(pool, conta, "Reforma solta", "reforma")
    o = og.com_comum(pool, conta, ob.obter_obra(pool, conta, solta["id"]))
    assert o["custos"]["comum"] == 0


def test_por_na_quadra_tira_o_lancamento_do_sem_obra(pool, conta):
    g, _ = _quadra(pool, conta, nome="Quadra 6")
    lid = _lanc(pool, conta, 50_000)
    assert any(i["id"] == lid for i in ob.sem_obra(pool, conta)["itens"])
    og.por_na_quadra(pool, conta, lid, g["id"])
    assert not any(i["id"] == lid for i in ob.sem_obra(pool, conta)["itens"])


# ── o agente ──────────────────────────────────────────────────────────────
def test_o_agente_poe_no_comum_e_fala_do_custo_cheio(pool, conta, monkeypatch):
    g, (grande, _) = _quadra(pool, conta, nome="Quadra 7")
    monkeypatch.setattr(tools_pj, "_nicho_da_conta", lambda p, c: "construcao")
    f = {x.nome: x for x in tools_pj.construir_ferramentas_pj(pool, conta)}
    txt = f["por_na_quadra"].executar({"lancamento_id": _lanc(pool, conta, 900_000), "quadra": "quadra 7"})
    assert "CUSTO COMUM de Quadra 7" in txt
    assert "Inclui R$ 6.000,00 do custo comum" in f["consultar_obra"].executar({"obra": "quadra 7 casa 1"})
    assert "por_na_quadra" in ob.bloco_persona(pool, conta)


# ── o lembrete de segunda, agrupado ───────────────────────────────────────
def test_o_lembrete_junta_as_casas_da_quadra(pool, conta):
    g = og.criar_grupo(pool, conta, "Quadra 8")
    for lote in ("2", "1"):
        o = ob.criar_obra(pool, conta, f"Casa Q8-{lote}", "casa",
                          inicio_em=HOJE - timedelta(days=40))       # CNO atrasado
        og.definir(pool, conta, o["id"], g["id"], lote)
    ob.criar_obra(pool, conta, "Casa avulsa", "casa", inicio_em=HOJE - timedelta(days=40))
    itens = obl.pendencias(pool, conta)["itens"]
    assert itens[0].startswith("Quadra 8 — Lote 1: CNO atrasado") and "; Lote 2: CNO atrasado" in itens[0]
    assert itens[1].startswith("Casa avulsa: CNO atrasado")


# ── o painel ──────────────────────────────────────────────────────────────
def _painel(pool, conta, monkeypatch):
    monkeypatch.setattr(po, "get_pool", lambda: pool)
    linha = [None] * 17
    linha[0] = conta
    monkeypatch.setattr(po, "conta_logada", lambda request: tuple(linha))
    monkeypatch.setattr(po, "nicho_da_conta", lambda c: "construcao")
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="t")
    app.include_router(po.router)

    @app.post("/_entrar")
    async def _entrar(request: Request):
        request.session["papel"] = "dono"
        return {"ok": True}

    c = TestClient(app, follow_redirects=False)
    c.post("/_entrar", json={})
    return c


def test_painel_mapa_comum_e_custo_cheio(pool, conta, monkeypatch):
    g, (grande, pequena) = _quadra(pool, conta, nome="Quadra 9")
    with pool.connection() as cx:
        cx.execute("update obras set inicio_em=%s where id=%s", (HOJE - timedelta(days=40), grande))
        cx.commit()
    c = _painel(pool, conta, monkeypatch)
    lid = _lanc(pool, conta, 900_000)
    assert f'value="q{g["id"]}"' in c.get("/painel/obras").text
    r = c.post("/painel/obras/lancamento", data={"lancamento_id": str(lid), "obra_id": f"q{g['id']}"})
    assert "erro" not in r.headers["location"]
    html = c.get(f"/painel/obras/quadra/{g['id']}").text
    assert "O mapa" in html and "qm-alerta" in html and "CNO atrasado" in html
    assert "Custo comum · R$ 9.000,00" in html and "R$ 6.000,00" in html and "R$ 3.000,00" in html
    ficha = c.get(f"/painel/obras/{pequena}").text
    assert "Custo comum da Quadra 9 (pelo m²): <b>R$ 3.000,00</b>" in ficha
    assert "Custo cheio da casa: <b>R$ 3.000,00</b>" in ficha
