"""A mão de obra paga por etapa (finance/obra_empreita.py, migração 371).

Os testes que mais importam:

`test_pagar_etapa_nao_feita_avisa_adiantamento` e
`test_pagar_de_novo_a_mesma_etapa_avisa` — são os dois jeitos de perder dinheiro
com empreiteiro, e o sistema avisa sem impedir (pode ser combinado).

`test_so_lancamento_desta_obra` — o pagamento da Casa 3 não fecha etapa da Casa 2.
"""
import os
from datetime import date
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool
from starlette.middleware.sessions import SessionMiddleware

from db.conexao import init_schema
from finance import obra_empreita as oe
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
              "367_pix_da_empresa.sql", "369_obra_fotos.sql", "371_obra_etapa_pagamentos.sql")
_BASE = Path(__file__).resolve().parent.parent / "db" / "migracoes"


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_obra_empreita_test"
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


def _casa(pool, conta, nome="Casa 2"):
    return ob.obter_obra(pool, conta, ob.criar_obra(pool, conta, nome, "casa")["id"])


def _mao_de_obra(pool, conta, obra, valor, descricao="Empreiteiro Zé"):
    with pool.connection() as c:
        lid = c.execute(
            """insert into lancamentos (conta_id, tipo, valor_centavos, categoria, descricao, data,
                                        natureza, centro_custo_id)
               values (%s,'despesa',%s,'Servicos',%s,%s,'empresa',%s) returning id""",
            (conta, valor, descricao, date.today(), obra["centro_custo_id"] if obra else None)
        ).fetchone()[0]
        c.commit()
    return lid


def _feita(pool, conta, obra, *nomes):
    for n in nomes:
        ob.marcar_etapa(pool, conta, obra["id"], n)


def test_divide_pelo_peso_das_etapas(pool, conta):
    casa = _casa(pool, conta)
    _feita(pool, conta, casa, "fundação", "estrutura")
    lid = _mao_de_obra(pool, conta, casa, 1_000_000)
    r = oe.pagar_etapas(pool, conta, casa["id"], ["fundação", "até a laje"], lancamento_id=lid)
    pesos = {e["nome"]: e["peso"] for e in casa["etapas"]}
    fund, estr = pesos["Preliminares e fundação"], pesos["Estrutura"]
    esperado_fund = int(1_000_000 * fund // (fund + estr))
    assert [(x["nome"], x["valor_centavos"]) for x in r["etapas"]] == [
        ("Preliminares e fundação", esperado_fund), ("Estrutura", 1_000_000 - esperado_fund)]
    assert r["adiantadas"] == [] and r["ja_pagas"] == []
    sit = oe.situacao(pool, conta, ob.obter_obra(pool, conta, casa["id"]))
    assert sit["total_pago"] == 1_000_000 and sit["pagas_nao_feitas"] == []


def test_pagar_etapa_nao_feita_avisa_adiantamento(pool, conta):
    casa = _casa(pool, conta)
    lid = _mao_de_obra(pool, conta, casa, 300_000)
    r = oe.pagar_etapas(pool, conta, casa["id"], ["alvenaria"], lancamento_id=lid)
    assert r["adiantadas"] == ["Alvenaria"]
    sit = oe.situacao(pool, conta, ob.obter_obra(pool, conta, casa["id"]))
    assert sit["pagas_nao_feitas"] == ["Alvenaria"]
    assert "adiantamento" in oe.resumo(casa, sit)


def test_pagar_de_novo_a_mesma_etapa_avisa(pool, conta):
    casa = _casa(pool, conta)
    _feita(pool, conta, casa, "pintura")
    oe.pagar_etapas(pool, conta, casa["id"], ["pintura"],
                    lancamento_id=_mao_de_obra(pool, conta, casa, 100_000))
    r = oe.pagar_etapas(pool, conta, casa["id"], ["pintura"],
                        lancamento_id=_mao_de_obra(pool, conta, casa, 100_000))
    assert len(r["ja_pagas"]) == 1 and "Pintura já tinha R$ 1.000,00 pago" in r["ja_pagas"][0]


def test_o_mesmo_lancamento_nao_paga_duas_vezes(pool, conta):
    casa = _casa(pool, conta)
    lid = _mao_de_obra(pool, conta, casa, 100_000)
    oe.pagar_etapas(pool, conta, casa["id"], ["pintura"], lancamento_id=lid)
    with pytest.raises(ValueError, match="já foi usado"):
        oe.pagar_etapas(pool, conta, casa["id"], ["pisos"], lancamento_id=lid)


def test_so_lancamento_desta_obra(pool, conta):
    casa2, casa3 = _casa(pool, conta), _casa(pool, conta, "Casa 3")
    with pytest.raises(ValueError, match="não está na Casa 2"):
        oe.pagar_etapas(pool, conta, casa2["id"], ["pintura"],
                        lancamento_id=_mao_de_obra(pool, conta, casa3, 100_000))
    with pytest.raises(ValueError, match="não está na Casa 2"):
        oe.pagar_etapas(pool, conta, casa2["id"], ["pintura"],
                        lancamento_id=_mao_de_obra(pool, conta, None, 100_000))


def test_lancamento_dividido_paga_so_a_parte_da_obra(pool, conta):
    casa2, casa3 = _casa(pool, conta), _casa(pool, conta, "Casa 3")
    lid = _mao_de_obra(pool, conta, None, 200_001)
    ob.dividir(pool, conta, lid, [casa2["id"], casa3["id"]])
    r = oe.pagar_etapas(pool, conta, casa3["id"], ["pintura"], lancamento_id=lid)
    assert r["etapas"][0]["valor_centavos"] in (100_000, 100_001)


def test_etapa_que_nao_existe_e_sem_etapa(pool, conta):
    casa = _casa(pool, conta)
    lid = _mao_de_obra(pool, conta, casa, 100_000)
    with pytest.raises(ValueError, match="Não achei a etapa"):
        oe.pagar_etapas(pool, conta, casa["id"], ["piscina"], lancamento_id=lid)
    with pytest.raises(ValueError, match="quais etapas"):
        oe.pagar_etapas(pool, conta, casa["id"], [], lancamento_id=lid)


def test_desfazer_tira_a_marca_e_deixa_o_dinheiro(pool, conta):
    casa = _casa(pool, conta)
    lid = _mao_de_obra(pool, conta, casa, 100_000)
    oe.pagar_etapas(pool, conta, casa["id"], ["pintura"], lancamento_id=lid)
    assert oe.desfazer(pool, conta, casa["id"], lid) == 1
    assert not oe.situacao(pool, conta, ob.obter_obra(pool, conta, casa["id"]))["tem"]
    assert ob.obter_obra(pool, conta, casa["id"])["custos"]["total"] == 100_000


# ── o agente e o painel ───────────────────────────────────────────────────
def test_o_agente_marca_e_avisa(pool, conta, monkeypatch):
    casa = _casa(pool, conta)
    lid = _mao_de_obra(pool, conta, casa, 1_000_000)
    monkeypatch.setattr(tools_pj, "_nicho_da_conta", lambda p, c: "construcao")
    f = {x.nome: x for x in tools_pj.construir_ferramentas_pj(pool, conta)}
    txt = f["pagar_etapa"].executar({"obra": "casa 2", "etapas": "fundação e estrutura",
                                     "lancamento_id": lid})
    assert txt.startswith("Marquei como PAGAS na Casa 2: preliminares e fundação")
    assert "foi adiantamento?" in txt
    assert "Mão de obra paga por etapa: R$ 10.000,00" in f["consultar_obra"].executar({"obra": "casa 2"})
    assert "PAGAMENTO DO EMPREITEIRO POR ETAPA" in ob.bloco_persona(pool, conta)


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


def test_marcar_e_desfazer_pelo_painel(pool, conta, monkeypatch):
    casa = _casa(pool, conta)
    _feita(pool, conta, casa, "pintura")
    lid = _mao_de_obra(pool, conta, casa, 250_000, "Pintor João")
    c = _painel(pool, conta, monkeypatch)
    html = c.get(f"/painel/obras/{casa['id']}").text
    assert "Mão de obra por etapa" in html and "Pintor João" in html
    pintura = next(e["id"] for e in casa["etapas"] if e["chave"] == "pintura")
    pisos = next(e["id"] for e in casa["etapas"] if e["chave"] == "pisos")
    r = c.post(f"/painel/obras/{casa['id']}/etapa-paga",
               data={"lancamento_id": str(lid), "etapa": [str(pintura), str(pisos)]})
    assert r.status_code == 303 and "erro" not in r.headers["location"]
    html = c.get(f"/painel/obras/{casa['id']}").text
    assert "Pago por etapa: <b>R$ 2.500,00</b>" in html
    assert "adiantamento): pisos" in html and ">pago R$" in html and ">adiantado R$" in html
    c.post(f"/painel/obras/{casa['id']}/etapa-paga/{lid}/desfazer")
    assert not oe.situacao(pool, conta, ob.obter_obra(pool, conta, casa["id"]))["tem"]
