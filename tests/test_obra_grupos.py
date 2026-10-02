"""As obras por quadra (finance/obra_grupos.py, migração 478). Desenho aprovado
em 01/10/2026: docs/mockups/obras_por_quadra.html.

Os testes que mais importam:

`test_marca_so_nas_que_comecaram_e_diz_quem_ficou_de_fora` — marcar fundação num
lote vazio mentiria o andamento da quadra.

`test_desfaz_volta_so_a_ultima_marcacao` — o "desfaz" é o que deixa marcar na hora
sem perguntar (decisão 4 do dono).

`test_pagamento_da_quadra_divide_pelo_m2_e_marca_em_cada_casa` — o mesmo lançamento
fecha a etapa de várias casas; a trava de "lançamento já usado" é por obra.
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
from finance import obra_empreita as oe
from finance import obra_grupos as og
from finance import obras as ob
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
              "367_pix_da_empresa.sql", "369_obra_fotos.sql", "371_obra_etapa_pagamentos.sql",
              "478_obra_quadras.sql")
_BASE = Path(__file__).resolve().parent.parent / "db" / "migracoes"
ONTEM = date.today() - timedelta(days=1)


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_obra_grupos_test"
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


def _quadra(pool, conta, nome="Quadra 5", lotes=(1, 2, 3), comecaram=(1, 2), area=45):
    g = og.criar_grupo(pool, conta, nome, "Residencial Lago Azul")
    ids = {}
    for n in lotes:
        o = ob.criar_obra(pool, conta, f"{nome} Lote {n}", "casa", area_m2=area,
                          inicio_em=ONTEM if n in comecaram else None)
        og.definir(pool, conta, o["id"], g["id"], str(n))
        ids[n] = o["id"]
    return g, ids


def _lanc(pool, conta, valor, categoria="Insumos"):
    with pool.connection() as c:
        lid = c.execute("""insert into lancamentos (conta_id, tipo, valor_centavos, categoria, descricao,
                                                    data, natureza)
                           values (%s,'despesa',%s,%s,'nota',%s,'empresa') returning id""",
                        (conta, valor, categoria, date.today())).fetchone()[0]
        c.commit()
    return lid


# ── a quadra ──────────────────────────────────────────────────────────────
def test_nome_e_rotulo(pool, conta):
    og.criar_grupo(pool, conta, "Quadra 4")
    with pytest.raises(ValueError, match="Já existe"):
        og.criar_grupo(pool, conta, "quadra 4")
    assert og.rotulo(pool, conta) == "Quadra"
    assert og.salvar_rotulo(pool, conta, "Setor") == "Setor" and og.rotulo(pool, conta) == "Setor"
    og.salvar_rotulo(pool, conta, "")
    assert og.rotulo(pool, conta) == "Quadra"


@pytest.mark.parametrize("fala,esperado", [("quadra 5", "Quadra 5"), ("Q5", "Quadra 5"),
                                           ("5", "Quadra 5"), ("quadra 05", "Quadra 5"),
                                           ("quadra 9", None)])
def test_acha_a_quadra_do_jeito_que_a_pessoa_fala(pool, conta, fala, esperado):
    og.criar_grupo(pool, conta, "Quadra 5")
    og.criar_grupo(pool, conta, "Quadra 6")
    g = og.grupo_por_nome(pool, conta, fala)
    assert (g["nome"] if g else None) == esperado


def test_casas_na_ordem_do_lote_e_andamento_pelo_m2(pool, conta):
    g = og.criar_grupo(pool, conta, "Quadra 7")
    for lote, area in (("10", 90), ("2", 45)):
        o = ob.criar_obra(pool, conta, f"Casa {lote}", "casa", area_m2=area)
        og.definir(pool, conta, o["id"], g["id"], lote)
    casas = og.casas(pool, conta, g["id"])
    assert [o["lote"] for o in casas] == ["2", "10"]
    grande = casas[1]
    for e in grande["etapas"]:
        ob.marcar_etapa(pool, conta, grande["id"], e["id"])
    assert og.andamento(og.casas(pool, conta, g["id"])) == 67      # 100 × 90/135


def test_apagar_so_a_quadra_vazia(pool, conta):
    g, ids = _quadra(pool, conta, "Quadra 8", lotes=(1,))
    with pytest.raises(ValueError, match="Tire as casas"):
        og.apagar_grupo(pool, conta, g["id"])
    og.definir(pool, conta, ids[1], None)
    og.apagar_grupo(pool, conta, g["id"])
    assert og.listar_grupos(pool, conta) == []


def test_quadra_de_outra_conta_nao_entra(pool, conta):
    with pool.connection() as c:
        outra = c.execute("insert into contas (tipo, nome) values ('pj','Outra') returning id").fetchone()[0]
        c.commit()
    g = og.criar_grupo(pool, outra, "Quadra 1")
    o = ob.criar_obra(pool, conta, "Casa X", "casa")
    with pytest.raises(ValueError, match="não é desta conta"):
        og.definir(pool, conta, o["id"], g["id"], "1")


# ── a marcação em lote ────────────────────────────────────────────────────
def test_marca_so_nas_que_comecaram_e_diz_quem_ficou_de_fora(pool, conta):
    g, ids = _quadra(pool, conta)
    r = og.marcar_etapa_grupo(pool, conta, g["id"], "fundação")
    assert r["etapa"] == "Preliminares e fundação"
    assert r["marcadas"] == ["Lote 1", "Lote 2"] and r["fora"] == ["Lote 3"]
    assert r["pct"] == 5                                              # 8% em 2 de 3 casas iguais
    de_novo = og.marcar_etapa_grupo(pool, conta, g["id"], "fundação")
    assert de_novo["marcadas"] == [] and de_novo["ja_feitas"] == ["Lote 1", "Lote 2"]


def test_escolhendo_as_casas_marca_mesmo_quem_nao_comecou(pool, conta):
    g, ids = _quadra(pool, conta)
    r = og.marcar_etapa_grupo(pool, conta, g["id"], "fundacao", obra_ids=[ids[3]])
    assert r["marcadas"] == ["Lote 3"] and r["fora"] == []


def test_desfaz_volta_so_a_ultima_marcacao(pool, conta):
    g, ids = _quadra(pool, conta)
    og.marcar_etapa_grupo(pool, conta, g["id"], "fundação")
    og.marcar_etapa_grupo(pool, conta, g["id"], "laje")
    r = og.desfazer_ultima(pool, conta, g["id"])
    assert r["etapa"] == "Estrutura" and len(r["voltaram"]) == 2
    q = og.quadro(pool, conta, g["id"])
    assert q["celulas"][ids[1]]["preliminares_fundacao"] == "feita"
    assert q["celulas"][ids[1]]["estrutura"] == "falta"
    og.desfazer_ultima(pool, conta, g["id"])
    assert og.desfazer_ultima(pool, conta, g["id"]) is None


def test_etapa_que_ninguem_tem(pool, conta):
    g, _ = _quadra(pool, conta)
    with pytest.raises(ValueError, match="Nenhuma casa"):
        og.marcar_etapa_grupo(pool, conta, g["id"], "piscina")


# ── a nota e o pagamento da quadra ────────────────────────────────────────
def test_dividir_pelo_m2(pool, conta):
    g = og.criar_grupo(pool, conta, "Quadra 9")
    a = ob.criar_obra(pool, conta, "Casa A", "casa", area_m2=90)
    b = ob.criar_obra(pool, conta, "Casa B", "casa", area_m2=45)
    lid = _lanc(pool, conta, 150_001)
    partes = ob.dividir(pool, conta, lid, [a["id"], b["id"]], por="m2")
    assert [p["valor_centavos"] for p in partes] == [100_001, 50_000]
    sem_area = ob.criar_obra(pool, conta, "Casa C", "casa")
    lid2 = _lanc(pool, conta, 100)
    assert [p["valor_centavos"] for p in ob.dividir(pool, conta, lid2, [a["id"], sem_area["id"]], por="m2")] == [50, 50]


def test_pagamento_da_quadra_divide_pelo_m2_e_marca_em_cada_casa(pool, conta, monkeypatch):
    g, ids = _quadra(pool, conta, "Quadra 4", lotes=(1, 2), comecaram=(1,))
    lid = _lanc(pool, conta, 3_600_000, categoria="Servicos")
    monkeypatch.setattr(tools_pj, "_nicho_da_conta", lambda p, c: "construcao")
    f = {x.nome: x for x in tools_pj.construir_ferramentas_pj(pool, conta)}
    txt = f["pagar_etapa"].executar({"quadra": "quadra 4", "etapas": ["fundação"], "lancamento_id": lid})
    assert "entre as 2 casas de Quadra 4" in txt and "PAGAS em 2" in txt and "adiantamento" in txt
    for oid in ids.values():
        sit = oe.situacao(pool, conta, ob.obter_obra(pool, conta, oid))
        assert sit["total_pago"] == 1_800_000


def test_o_agente_marca_divide_e_desfaz(pool, conta, monkeypatch):
    g, ids = _quadra(pool, conta)
    monkeypatch.setattr(tools_pj, "_nicho_da_conta", lambda p, c: "construcao")
    f = {x.nome: x for x in tools_pj.construir_ferramentas_pj(pool, conta)}
    txt = f["marcar_etapa_quadra"].executar({"quadra": "quadra 5", "etapa": "fundação"})
    assert "em 2 casa(s) de Quadra 5" in txt and "ainda não começaram: Lote 3" in txt and "desfaz" in txt
    txt = f["marcar_etapa_quadra"].executar({"quadra": "5", "etapa": "laje", "lotes": ["3"]})
    assert "em 1 casa(s) de Quadra 5 (Lote 3)" in txt
    assert "voltou a ficar em aberto em 1 casa(s)" in f["desfazer_etapa_quadra"].executar({"quadra": "quadra 5"})
    lid = _lanc(pool, conta, 300)
    assert f["dividir_entre_obras"].executar({"lancamento_id": lid, "quadra": "quadra 5"}).count("R$ 1,00") == 3
    assert "Não achei" in f["marcar_etapa_quadra"].executar({"quadra": "quadra 99", "etapa": "laje"})
    assert "QUADRAS" in ob.bloco_persona(pool, conta)


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


def test_painel_lista_agrupada_quadro_e_marcacao(pool, conta, monkeypatch):
    c = _painel(pool, conta, monkeypatch)
    r = c.post("/painel/obras/quadra/nova", data={"nome": "Quadra 4", "empreendimento": "Lago Azul"})
    gid = int(r.headers["location"].rsplit("/", 1)[1])
    r = c.post("/painel/obras/nova", data={"nome": "Casa 1", "tipo": "casa", "area_m2": "45",
                                          "inicio_em": ONTEM.isoformat(), "grupo_id": str(gid), "lote": "1"})
    oid = int(r.headers["location"].rsplit("/", 1)[1])
    solta = ob.criar_obra(pool, conta, "Reforma avulsa", "reforma")
    html = c.get("/painel/obras").text
    assert 'href="/painel/obras/quadra/%d"' % gid in html and "Sem quadra · 1" in html and "Lote 1" in html
    html = c.get(f"/painel/obras/quadra/{gid}").text
    assert "Quadro de etapas" in html and 'name="obra" value="%d" style="width:auto" checked' % oid in html
    r = c.post(f"/painel/obras/quadra/{gid}/marcar", data={"etapa": "estrutura", "obra": [str(oid)]})
    assert "ok=" in r.headers["location"]
    assert og.quadro(pool, conta, gid)["celulas"][oid]["estrutura"] == "feita"
    c.post(f"/painel/obras/quadra/{gid}/desfazer")
    assert og.quadro(pool, conta, gid)["celulas"][oid]["estrutura"] == "falta"
    assert "erro=" in c.post(f"/painel/obras/quadra/{gid}/marcar", data={"etapa": "estrutura"}).headers["location"]
    # a ficha da casa mostra a quadra e muda pelo formulário
    assert "Quadra 4</a> · Lote 1" in c.get(f"/painel/obras/{oid}").text
    c.post(f"/painel/obras/{solta['id']}/editar", data={"nome": "Reforma avulsa", "tipo": "reforma",
                                                       "status": "em_obra", "grupo_id": str(gid), "lote": "9"})
    assert og.por_obra(pool, conta)[solta["id"]] == {"grupo_id": gid, "lote": "9"}


def test_conta_sem_quadra_ve_a_lista_de_sempre(pool, conta, monkeypatch):
    ob.criar_obra(pool, conta, "Casa só", "casa")
    html = _painel(pool, conta, monkeypatch).get("/painel/obras").text
    assert "Casa só" in html and "Sem quadra" not in html and "+ Nova quadra" in html


def test_quadra_de_outra_conta_nao_abre(pool, conta, monkeypatch):
    with pool.connection() as c:
        outra = c.execute("insert into contas (tipo, nome) values ('pj','Outra') returning id").fetchone()[0]
        c.commit()
    g = og.criar_grupo(pool, outra, "Quadra secreta")
    c = _painel(pool, conta, monkeypatch)
    assert c.get(f"/painel/obras/quadra/{g['id']}").status_code == 303
    assert c.post(f"/painel/obras/quadra/{g['id']}/desfazer").headers["location"] == "/painel/obras"
