"""O orçamento da reforma (finance/obra_reforma.py, migração 355) e o link do cliente.

A PX2 (conta 33) também faz reforma pra cliente. O desenho está em
docs/mockups/nicho_construcao.html, seção 09.

Os testes que mais importam:

`test_aceito_o_orcamento_nao_muda_mais` — serviço extra depois do aceite é
ADITIVO, com aceite próprio (CC, art. 619). Mudar o orçamento aceito seria
cobrar o que o cliente não aceitou.

`test_a_etapa_feita_libera_a_parcela_dela` — é o que o agente oferece cobrar.

`test_o_link_publico_escapa_o_que_foi_digitado` — a página é pública.
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
from finance import empresa as emp
from finance import obra_reforma as orf
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
              "353_obra_venda_documentos.sql", "355_reforma_orcamento.sql")
_BASE = Path(__file__).resolve().parent.parent / "db" / "migracoes"
HOJE = date.today()
ITENS = [
    {"servico": "Demolição do piso antigo", "tipo": "mao_de_obra", "unidade": "m2",
     "quantidade": 20, "valor_unit_centavos": 2_500},
    {"servico": "Porcelanato 60x60", "tipo": "material", "unidade": "m2",
     "quantidade": 22, "valor_unit_centavos": 8_990},
    {"servico": "Assentamento", "tipo": "mao_de_obra", "unidade": "m2",
     "quantidade": 20, "valor_unit_centavos": 4_500},
    {"servico": "Betoneira", "tipo": "equipamento", "unidade": "diaria",
     "quantidade": 2, "valor_unit_centavos": 6_000},
]
TOTAL = 20 * 2_500 + 22 * 8_990 + 20 * 4_500 + 2 * 6_000     # 349.780


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_obra_reforma_test"
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


def _reforma(pool, conta, nome="Reforma Dona Márcia"):
    return ob.criar_obra(pool, conta, nome, "reforma", endereco="Rua A, 10")


def _enviado(pool, conta, modelo="etapas"):
    o = _reforma(pool, conta)
    orc = orf.salvar_rascunho(pool, conta, o["id"], itens=ITENS, modelo_pagamento=modelo,
                              escopo="Piso da sala e cozinha.")
    return o, orf.enviar(pool, conta, orc["id"])


def _titulos(pool, ids):
    with pool.connection() as c:
        return [dict(zip(("descricao", "contraparte", "valor", "centro", "plano", "status"), r))
                for r in c.execute(
                    """select t.descricao, t.contraparte, t.valor_centavos, t.centro_custo_id,
                              p.codigo, t.status
                         from titulos t left join plano_contas p on p.id = t.plano_conta_id
                        where t.id = any(%s) order by t.id""", (list(ids),)).fetchall()]


# ── as contas do orçamento ────────────────────────────────────────────────
def test_mao_de_obra_material_e_equipamento_separados():
    t = orf.totais(orf.limpar_itens(ITENS + [{"servico": "  ", "quantidade": 9}]))
    assert (t["mao_de_obra"], t["material"], t["equipamento"]) == (140_000, 197_780, 12_000)
    assert t["total"] == TOTAL


def test_as_parcelas_somam_o_total_ate_o_centavo():
    ps = orf.valorar_parcelas([{"rotulo": "a", "pct": 33.33}, {"rotulo": "b", "pct": 33.33},
                               {"rotulo": "c", "pct": 33.34}], 100_001)
    assert sum(p["valor_centavos"] for p in ps) == 100_001


def test_parcelas_que_nao_somam_100_nao_passam():
    with pytest.raises(ValueError, match="100%"):
        orf.valorar_parcelas([{"rotulo": "a", "pct": 50}], 1_000)


def test_os_modelos_ligam_as_parcelas_as_etapas(pool, conta):
    etapas = _reforma(pool, conta)["etapas"]
    rcb = orf.parcelas_do_modelo("rcb", etapas)
    assert [p["pct"] for p in rcb] == [50, 40, 10]
    assert [p["etapa"] for p in rcb] == [None, "alvenaria_reboco", "limpeza_entrega"]
    assert [p["pct"] for p in orf.parcelas_do_modelo("etapas", etapas)] == [30, 40, 30]
    assert orf.parcelas_do_modelo("avista", etapas) == [
        {"rotulo": "Na assinatura", "pct": 100, "etapa": None}]


# ── o rascunho ────────────────────────────────────────────────────────────
def test_o_rascunho_nasce_e_muda_no_mesmo_lugar(pool, conta):
    o = _reforma(pool, conta)
    a = orf.salvar_rascunho(pool, conta, o["id"], itens=ITENS)
    b = orf.salvar_rascunho(pool, conta, o["id"], itens=ITENS[:1])
    assert a["id"] == b["id"] and b["versao"] == 1 and b["total_centavos"] == 50_000


def test_orcamento_com_aceite_e_da_reforma(pool, conta):
    casa = ob.criar_obra(pool, conta, "Casa 1", "casa")
    with pytest.raises(ValueError, match="reforma"):
        orf.salvar_rascunho(pool, conta, casa["id"], itens=ITENS)


def test_sem_servico_nao_tem_orcamento(pool, conta):
    o = _reforma(pool, conta)
    with pytest.raises(ValueError, match="serviço"):
        orf.salvar_rascunho(pool, conta, o["id"], itens=[{"servico": ""}])


def test_reforma_casa_brasil_nasce_com_55_dias(pool, conta):
    o = _reforma(pool, conta)
    assert orf.salvar_rascunho(pool, conta, o["id"], itens=ITENS,
                               modelo_pagamento="rcb")["prazo_dias"] == 55


def test_o_link_vale_10_dias(pool, conta):
    _o, env = _enviado(pool, conta)
    assert env["status"] == "enviado" and len(env["token"]) >= 20
    assert env["validade_ate"] == HOJE + timedelta(days=10)


# ── o aceite ──────────────────────────────────────────────────────────────
def test_o_aceite_vira_contas_a_receber_na_obra(pool, conta):
    o, env = _enviado(pool, conta)
    assert orf.aceitar(pool, env["token"], "Márcia Souza", "123", "200.1.1.1")
    v = orf.orcamentos(pool, conta, o["id"])[0]
    assert v["status"] == "aceito" and v["aceito_nome"] == "Márcia Souza"
    ts = _titulos(pool, v["titulos"])
    assert [t["valor"] for t in ts] == [104_934, 139_912, 104_934]
    assert {t["plano"] for t in ts} == {"1.1.02"}
    assert {t["centro"] for t in ts} == {o["centro_custo_id"]}
    assert {t["contraparte"] for t in ts} == {"Márcia Souza"}
    assert ob.obter_obra(pool, conta, o["id"])["valor_centavos"] == TOTAL


def test_aceitar_duas_vezes_nao_duplica(pool, conta):
    o, env = _enviado(pool, conta)
    assert orf.aceitar(pool, env["token"], "Márcia", "", "")
    assert not orf.aceitar(pool, env["token"], "Márcia", "", "")
    assert len(orf.orcamentos(pool, conta, o["id"])[0]["titulos"]) == 3


def test_link_vencido_nao_aceita(pool, conta):
    _o, env = _enviado(pool, conta)
    with pool.connection() as c:
        c.execute("update obra_orcamentos set validade_ate=%s where id=%s",
                  (HOJE - timedelta(days=1), env["id"]))
        c.commit()
    assert not orf.aceitar(pool, env["token"], "Márcia", "", "")
    assert orf.por_token(pool, env["token"])["vencido"] is True


def test_aceito_o_orcamento_nao_muda_mais(pool, conta):
    o, env = _enviado(pool, conta)
    orf.aceitar(pool, env["token"], "Márcia", "", "")
    with pytest.raises(ValueError, match="aditivo"):
        orf.salvar_rascunho(pool, conta, o["id"], itens=ITENS[:1])


def test_recusado_volta_a_rascunho_ao_mexer(pool, conta):
    o, env = _enviado(pool, conta)
    assert orf.recusar(pool, env["token"])
    assert orf.salvar_rascunho(pool, conta, o["id"], itens=ITENS[:2])["status"] == "rascunho"


# ── o aditivo ─────────────────────────────────────────────────────────────
def test_aditivo_so_depois_do_aceite(pool, conta):
    o, _env = _enviado(pool, conta)
    with pytest.raises(ValueError, match="aceito"):
        orf.abrir_aditivo(pool, conta, o["id"])


def test_o_aditivo_tem_link_aceite_e_parcelas_proprios(pool, conta):
    o, env = _enviado(pool, conta)
    orf.aceitar(pool, env["token"], "Márcia", "", "")
    assert orf.abrir_aditivo(pool, conta, o["id"]) == 2
    extra = [{"servico": "Rodapé", "tipo": "mao_de_obra", "unidade": "m",
              "quantidade": 10, "valor_unit_centavos": 3_000}]
    ad = orf.salvar_rascunho(pool, conta, o["id"], itens=extra, modelo_pagamento="avista")
    assert ad["versao"] == 2 and ad["total_centavos"] == 30_000
    ad = orf.enviar(pool, conta, ad["id"])
    assert ad["token"] != env["token"]
    assert orf.aceitar(pool, ad["token"], "Márcia", "", "")
    ts = _titulos(pool, orf.orcamentos(pool, conta, o["id"])[1]["titulos"])
    assert ts[0]["valor"] == 30_000 and "(aditivo 1)" in ts[0]["descricao"]
    assert ob.obter_obra(pool, conta, o["id"])["valor_centavos"] == TOTAL + 30_000


# ── a cobrança por etapa ──────────────────────────────────────────────────
def test_a_etapa_feita_libera_a_parcela_dela(pool, conta):
    o, env = _enviado(pool, conta)
    orf.aceitar(pool, env["token"], "Márcia", "", "")
    assert orf.parcelas_liberadas(pool, conta, ob.obter_obra(pool, conta, o["id"])) == []
    ob.marcar_etapa(pool, conta, o["id"], "alvenaria e reboco")
    lib = orf.parcelas_liberadas(pool, conta, ob.obter_obra(pool, conta, o["id"]))
    assert [(p["rotulo"], p["valor_centavos"]) for p in lib] == [("Na metade da obra", 139_912)]
    emp.dar_baixa_titulo(pool, conta, lib[0]["titulo_id"])
    assert orf.parcelas_liberadas(pool, conta, ob.obter_obra(pool, conta, o["id"])) == []


def test_o_agente_avisa_que_pode_cobrar(pool, conta, monkeypatch):
    o, env = _enviado(pool, conta)
    orf.aceitar(pool, env["token"], "Márcia", "", "")
    monkeypatch.setattr(tools_pj, "_nicho_da_conta", lambda p, c: "construcao")
    f = {x.nome: x for x in tools_pj.construir_ferramentas_pj(pool, conta)}
    txt = f["marcar_etapa"].executar({"obra": "reforma", "etapa": "reboco"})
    assert 'A parcela "Na metade da obra" (R$ 1.399,12) já pode ser cobrada' in txt
    assert "PARCELAS PRA COBRAR" in ob.bloco_persona(pool, conta)


# ── o link público ────────────────────────────────────────────────────────
def _app(pool, monkeypatch):
    monkeypatch.setattr(po, "get_pool", lambda: pool)
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="t")
    app.include_router(po.router)
    return TestClient(app, follow_redirects=False)


def test_o_cliente_ve_o_orcamento_e_aceita(pool, conta, monkeypatch):
    o, env = _enviado(pool, conta)
    c = _app(pool, monkeypatch)
    html = c.get(f"/orcamento-obra/{env['token']}").text
    assert "PX2 Empreendimentos" in html and "Porcelanato 60x60" in html
    assert "R$ 3.497,80" in html and "Aceitar o orçamento" in html
    assert "Serviço extra" in html and "Piso da sala e cozinha." in html
    r = c.post(f"/orcamento-obra/{env['token']}/aceitar", data={"nome": "Márcia Souza"})
    assert "erro=" in r.headers["location"]                  # sem marcar o "li e aceito"
    r = c.post(f"/orcamento-obra/{env['token']}/aceitar",
               data={"nome": "Márcia Souza", "doc": "", "concordo": "1"})
    assert r.headers["location"].endswith("?ok=aceito")
    assert "Aceito por <b>Márcia Souza</b>" in c.get(r.headers["location"]).text
    assert orf.orcamentos(pool, conta, o["id"])[0]["status"] == "aceito"


def test_link_que_nao_existe_e_404(pool, monkeypatch):
    assert _app(pool, monkeypatch).get("/orcamento-obra/nao-existe-este-token").status_code == 404


def test_o_link_publico_escapa_o_que_foi_digitado(pool, conta, monkeypatch):
    o = _reforma(pool, conta, "Reforma <b>x</b>")
    orc = orf.salvar_rascunho(pool, conta, o["id"],
                              itens=[{"servico": "<script>alert(1)</script>", "quantidade": 1,
                                      "valor_unit_centavos": 100}])
    env = orf.enviar(pool, conta, orc["id"])
    html = _app(pool, monkeypatch).get(f"/orcamento-obra/{env['token']}").text
    assert "<script>alert(1)" not in html and "&lt;script&gt;" in html


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


def test_montar_mandar_e_abrir_aditivo_pela_ficha(pool, conta, monkeypatch):
    o = _reforma(pool, conta)
    c = _painel(pool, conta, monkeypatch)
    assert "Montar o orçamento" in c.get(f"/painel/obras/{o['id']}").text
    r = c.post(f"/painel/obras/{o['id']}/orcamento", data={
        "servico": ["Pintura da sala", ""], "tipo": ["mao_de_obra", "mao_de_obra"],
        "unidade": ["m2", "m2"], "quantidade": ["40", ""], "valor_unit": ["18,50", ""],
        "material_incluso": "0", "prazo_dias": "", "modelo_pagamento": "rcb",
        "usar_modelo": "1", "escopo": "Só a sala.", "garantia": ""})
    assert r.status_code == 303
    v = orf.orcamentos(pool, conta, o["id"])[0]
    assert v["total_centavos"] == 74_000 and v["prazo_dias"] == 55 and not v["material_incluso"]
    c.post(f"/painel/obras/{o['id']}/orcamento/{v['id']}/enviar")
    v = orf.orcamentos(pool, conta, o["id"])[0]
    assert v["status"] == "enviado"
    assert f"/orcamento-obra/{v['token']}" in c.get(f"/painel/obras/{o['id']}").text
    orf.aceitar(pool, v["token"], "Márcia", "", "")
    c.post(f"/painel/obras/{o['id']}/aditivo")
    assert [x["versao"] for x in orf.orcamentos(pool, conta, o["id"])] == [1, 2]
