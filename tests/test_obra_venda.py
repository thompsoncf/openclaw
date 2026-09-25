"""A venda da casa e os papéis dela (finance/obra_venda.py, migração 353).

A PX2 (conta 33) constrói casa popular pra vender pelo Minha Casa Minha Vida. Na
casa pronta financiada, o dinheiro da Caixa só cai depois do registro — e antes
disso a casa precisa de habite-se, CND da obra e averbação. O desenho está em
docs/mockups/nicho_construcao.html, seções 06 e 10.

Os testes que mais importam:

`test_quem_paga_o_repasse_e_a_caixa` — o título do repasse não pode cair na ficha
do comprador: ele diria que o comprador deve R$ 145 mil que quem paga é o banco.

`test_os_titulos_nascem_na_assinatura_e_so_uma_vez` — antes da assinatura a venda
ainda cai; depois dela, andar a venda de novo não pode duplicar a cobrança.

`test_o_dinheiro_que_cai_aparece_na_ficha_da_casa` — a baixa do repasse vira
receita no centro da obra, e é isso que fecha a margem da casa.
"""
import os
from datetime import date, timedelta
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from finance import empresa as emp
from finance import obra_venda as ov
from finance import obras as ob
from finance import tools_pj

_MIGRACOES = ("018_chave_nfce_lancamentos.sql", "053_modulo_pj.sql",
              "057_natureza_lancamento.sql", "064_clientes_lojista.sql",
              "066_pessoas_identidade.sql", "067_titulos_cliente.sql",
              "131_pessoa_cnpj.sql", "132_plano_contas_centros_custo.sql",
              "143_plano_contas_locacao_buffet_servicos.sql", "182_clientes_papel.sql", "186_plano_aporte_socios.sql",
              "195_titulo_aprovacao.sql", "196_titulo_recorrencia.sql",
              "197_titulo_acrescimo.sql", "317_titulo_classificacao.sql",
              "325_tipo_despesa.sql", "336_plano_fardamentos.sql", "349_plano_obras.sql",
              "351_obras.sql", "353_obra_venda_documentos.sql")
_BASE = Path(__file__).resolve().parent.parent / "db" / "migracoes"
HOJE = date.today()


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_obra_venda_test"
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


def _casa(pool, conta, nome="Casa 2", pronta=False, **kw):
    o = ob.criar_obra(pool, conta, nome, "casa", **kw)
    if pronta:
        for e in o["etapas"]:
            ob.marcar_etapa(pool, conta, o["id"], e["id"])
    return ob.obter_obra(pool, conta, o["id"])


def _venda_padrao(pool, conta, obra_id, **kw):
    campos = dict(comprador="J. Silva", faixa=2, valor_venda_centavos=15_000_000,
                  financiamento_centavos=11_000_000, subsidio_centavos=2_500_000,
                  fgts_centavos=1_000_000)
    campos.update(kw)
    return ov.salvar_venda(pool, conta, obra_id, **campos)


def _papeis_da_venda(pool, conta, obra_id):
    for t in ("habite_se", "cnd_obra", "averbacao"):
        ov.marcar_documento(pool, conta, obra_id, t)


def _titulo(pool, tid):
    with pool.connection() as c:
        return dict(zip(("descricao", "contraparte", "valor_centavos", "vencimento", "cliente_id",
                         "centro_custo_id", "plano", "status"), c.execute(
            """select t.descricao, t.contraparte, t.valor_centavos, t.vencimento, t.cliente_id,
                      t.centro_custo_id, p.codigo, t.status
                 from titulos t left join plano_contas p on p.id = t.plano_conta_id
                where t.id=%s""", (tid,)).fetchone()))


# ── como se fala ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("fala,tipo", [
    ("saiu o habite-se", "habite_se"), ("habite se", "habite_se"), ("a CND", "cnd_obra"),
    ("averbou", "averbacao"), ("alvará", "alvara"), ("ART", "art_rrt"), ("cno", "cno"),
    ("certidão da junta", "certidoes"), ("matrícula", "matricula"), ("piscina", None),
])
def test_o_papel_do_jeito_que_se_fala(fala, tipo):
    assert ov.achar_documento(fala) == tipo


@pytest.mark.parametrize("fala,passo", [
    ("assinou o contrato", "assinatura"), ("registrou no cartório", "registro"),
    ("caiu o dinheiro", "creditado"), ("aprovou", "aprovado"), ("desistiu", "desistiu"),
    ("sei lá", None),
])
def test_o_passo_do_jeito_que_se_fala(fala, passo):
    assert ov.achar_passo(fala) == passo


# ── os papéis ─────────────────────────────────────────────────────────────
def test_os_oito_papeis_nascem_pendentes(pool, conta):
    o = _casa(pool, conta)
    docs = ov.documentos(pool, conta, o["id"])
    assert [d["tipo"] for d in docs] == [t for t, _n in ov.DOCUMENTOS]
    assert {d["status"] for d in docs} == {"pendente"}


def test_pelo_whatsapp_o_que_nao_veio_fica(pool, conta):
    o = _casa(pool, conta)
    ov.marcar_documento(pool, conta, o["id"], "alvara", status="pendente", numero="123/26",
                        vence_em=HOJE + timedelta(days=200))
    d = ov.marcar_documento(pool, conta, o["id"], "alvara")
    assert d["status"] == "ok" and d["numero"] == "123/26"
    assert d["vence_em"] == HOJE + timedelta(days=200) and d["emitido_em"] == HOJE


def test_pela_tela_vale_o_formulario_inteiro(pool, conta):
    o = _casa(pool, conta)
    ov.marcar_documento(pool, conta, o["id"], "alvara", vence_em=HOJE + timedelta(days=9))
    d = ov.marcar_documento(pool, conta, o["id"], "alvara", status="ok", numero="",
                            emitido_em=HOJE, vence_em=None, substituir=True)
    assert d["vence_em"] is None


# ── o caminho ─────────────────────────────────────────────────────────────
def test_casa_em_obra_trava_na_propria_obra(pool, conta):
    o = _casa(pool, conta)
    assert ov.situacao_da_casa(pool, conta, o)["trava"]["chave"] == "obra"


def test_casa_pronta_trava_no_primeiro_papel_que_falta(pool, conta):
    o = _casa(pool, conta, pronta=True)
    assert ov.situacao_da_casa(pool, conta, o)["trava"]["chave"] == "habite_se"
    ov.marcar_documento(pool, conta, o["id"], "habite_se")
    assert ov.situacao_da_casa(pool, conta, o)["trava"]["chave"] == "cnd_obra"
    ov.marcar_documento(pool, conta, o["id"], "cnd_obra")
    ov.marcar_documento(pool, conta, o["id"], "averbacao")
    assert ov.situacao_da_casa(pool, conta, o)["trava"]["chave"] == "aprovado"


def test_a_ordem_do_caminho_e_a_do_dinheiro(pool, conta):
    o = _casa(pool, conta)
    chaves = [p["chave"] for p in ov.situacao_da_casa(pool, conta, o)["caminho"]]
    assert chaves == ["obra", "habite_se", "cnd_obra", "averbacao", "aprovado", "avaliacao",
                      "assinatura", "registro", "creditado"]


def test_a_vista_pula_aprovacao_e_avaliacao(pool, conta):
    o = _casa(pool, conta)
    _venda_padrao(pool, conta, o["id"], modalidade="avista")
    chaves = [p["chave"] for p in ov.situacao_da_casa(pool, conta, o)["caminho"]]
    assert "aprovado" not in chaves and "avaliacao" not in chaves


# ── a venda ───────────────────────────────────────────────────────────────
def test_a_entrada_sai_da_conta(pool, conta):
    o = _casa(pool, conta)
    v = _venda_padrao(pool, conta, o["id"])
    assert v["entrada_centavos"] == 500_000 and v["repasse_centavos"] == 14_500_000


def test_venda_e_de_casa(pool, conta):
    r = ob.criar_obra(pool, conta, "Reforma", "reforma")
    with pytest.raises(ValueError, match="casa"):
        ov.salvar_venda(pool, conta, r["id"], comprador="X")


def test_o_comprador_vai_pra_base_de_clientes_uma_vez(pool, conta):
    o = _casa(pool, conta)
    v1 = _venda_padrao(pool, conta, o["id"])
    v2 = _venda_padrao(pool, conta, o["id"], obs="segunda vez")
    assert v1["cliente_id"] and v1["cliente_id"] == v2["cliente_id"]


def test_andar_sem_venda_pede_o_cadastro(pool, conta):
    o = _casa(pool, conta)
    with pytest.raises(ValueError, match="Cadastre a venda"):
        ov.andar_venda(pool, conta, o["id"], "assinatura")


def test_os_titulos_nascem_na_assinatura_e_so_uma_vez(pool, conta):
    o = _casa(pool, conta, pronta=True)
    _venda_padrao(pool, conta, o["id"])
    assert ov.andar_venda(pool, conta, o["id"], "aprovado")["titulos"] == []
    r = ov.andar_venda(pool, conta, o["id"], "assinatura", HOJE)
    assert r["titulos"] == ["entrada R$ 5.000,00", "repasse R$ 145.000,00"]
    assert ov.andar_venda(pool, conta, o["id"], "registro")["titulos"] == []
    assert ob.obter_obra(pool, conta, o["id"])["status"] == "vendida"


def test_quem_paga_o_repasse_e_a_caixa(pool, conta):
    o = _casa(pool, conta, pronta=True)
    _venda_padrao(pool, conta, o["id"])
    ov.andar_venda(pool, conta, o["id"], "assinatura", HOJE)
    v = ov.venda(pool, conta, o["id"])
    entrada, repasse = _titulo(pool, v["titulo_entrada_id"]), _titulo(pool, v["titulo_repasse_id"])
    assert entrada["contraparte"] == "J. Silva" and entrada["cliente_id"] == v["cliente_id"]
    assert entrada["vencimento"] == HOJE
    assert repasse["contraparte"] == "Caixa" and repasse["cliente_id"] is None
    assert repasse["vencimento"] == HOJE + timedelta(days=30)
    for t in (entrada, repasse):
        assert t["centro_custo_id"] == o["centro_custo_id"] and t["plano"] == "1.1.04"


def test_o_dinheiro_que_cai_aparece_na_ficha_da_casa(pool, conta):
    o = _casa(pool, conta, pronta=True)
    _venda_padrao(pool, conta, o["id"])
    ov.andar_venda(pool, conta, o["id"], "assinatura", HOJE)
    v = ov.venda(pool, conta, o["id"])
    assert emp.dar_baixa_titulo(pool, conta, v["titulo_repasse_id"])["ok"]
    assert ob.obter_obra(pool, conta, o["id"])["custos"]["recebido"] == 14_500_000


def test_venda_desfeita_devolve_a_casa(pool, conta):
    o = _casa(pool, conta, pronta=True)
    _venda_padrao(pool, conta, o["id"])
    ov.andar_venda(pool, conta, o["id"], "assinatura")
    ov.andar_venda(pool, conta, o["id"], "desistiu")
    assert ob.obter_obra(pool, conta, o["id"])["status"] == "pronta"
    assert ov.venda(pool, conta, o["id"])["situacao"] == "desistiu"


# ── os alertas ────────────────────────────────────────────────────────────
def test_cno_atrasado_e_cno_perto(pool, conta):
    o = _casa(pool, conta, inicio_em=HOJE - timedelta(days=40))
    assert any(a.startswith("CNO atrasado") for a in ov.situacao_da_casa(pool, conta, o)["alertas"])
    o2 = _casa(pool, conta, "Casa 3", inicio_em=HOJE - timedelta(days=25))
    assert "CNO vence em 5 dia(s)" in " ".join(ov.situacao_da_casa(pool, conta, o2)["alertas"])


def test_alvara_e_certidoes(pool, conta):
    o = _casa(pool, conta)
    ov.marcar_documento(pool, conta, o["id"], "alvara", vence_em=HOJE + timedelta(days=10))
    ov.marcar_documento(pool, conta, o["id"], "certidoes", emitido_em=HOJE - timedelta(days=200))
    txt = " ".join(ov.situacao_da_casa(pool, conta, o)["alertas"])
    assert "Alvará vence em 10 dia(s)" in txt and "Certidões da empresa vencidas" in txt


def test_avaliacao_abaixo_do_preco(pool, conta):
    o = _casa(pool, conta)
    _venda_padrao(pool, conta, o["id"], valor_avaliacao_centavos=14_200_000,
                  avaliacao_em=HOJE)
    txt = " ".join(ov.situacao_da_casa(pool, conta, o)["alertas"])
    assert "abaixo do preço" in txt and "R$ 8.000,00" in txt


def test_registro_atrasado(pool, conta):
    o = _casa(pool, conta, pronta=True)
    _venda_padrao(pool, conta, o["id"])
    ov.andar_venda(pool, conta, o["id"], "assinatura", HOJE - timedelta(days=35))
    assert any(a.startswith("Registro atrasado")
               for a in ov.situacao_da_casa(pool, conta, o)["alertas"])


def test_casa_pronta_parada_esperando_papel(pool, conta):
    o = _casa(pool, conta, pronta=True)
    assert "Pronta há 0 dia(s) esperando habite-se." in ov.situacao_da_casa(pool, conta, o)["alertas"]


# ── o dinheiro parado ─────────────────────────────────────────────────────
def test_parado_e_o_gasto_em_casa_que_a_caixa_nao_pagou(pool, conta):
    a = _casa(pool, conta, "Casa 1", pronta=True)
    b = _casa(pool, conta, "Casa 2")
    r = ob.criar_obra(pool, conta, "Reforma", "reforma")
    with pool.connection() as c:
        for obra, valor in ((a, 7_000_000), (b, 3_000_000), (r, 900_000)):
            c.execute("""insert into lancamentos (conta_id, tipo, valor_centavos, categoria,
                                                   descricao, data, natureza, centro_custo_id)
                         values (%s,'despesa',%s,'Insumos','x',%s,'empresa',%s)""",
                      (conta, valor, HOJE, obra["centro_custo_id"]))
        c.commit()
    assert ov.parado_em_casas(pool, conta, ob.listar_obras(pool, conta)) == 10_000_000
    _venda_padrao(pool, conta, a["id"])
    ov.andar_venda(pool, conta, a["id"], "creditado")
    assert ov.parado_em_casas(pool, conta, ob.listar_obras(pool, conta)) == 3_000_000


# ── o agente ──────────────────────────────────────────────────────────────
def _ferramentas(pool, conta, monkeypatch):
    monkeypatch.setattr(tools_pj, "_nicho_da_conta", lambda p, c: "construcao")
    return {f.nome: f for f in tools_pj.construir_ferramentas_pj(pool, conta)}


def test_saiu_o_habite_se_da_casa_2(pool, conta, monkeypatch):
    _casa(pool, conta, pronta=True)
    f = _ferramentas(pool, conta, monkeypatch)
    txt = f["marcar_documento"].executar({"obra": "casa 2", "documento": "habite-se"})
    assert txt == "Habite-se da Casa 2: em dia. ✅ Agora o que trava é cnd da obra."


def test_assinou_o_contrato_da_casa_2(pool, conta, monkeypatch):
    o = _casa(pool, conta, pronta=True)
    _venda_padrao(pool, conta, o["id"])
    f = _ferramentas(pool, conta, monkeypatch)
    txt = f["andar_venda"].executar({"obra": "Casa 2", "passo": "assinou o contrato"})
    assert txt.startswith("Casa 2: contrato assinado. ✅ Criei as contas a receber")
    assert "repasse R$ 145.000,00" in txt


def test_quanto_ja_gastei_fala_do_caminho_da_casa(pool, conta, monkeypatch):
    _casa(pool, conta, pronta=True)
    f = _ferramentas(pool, conta, monkeypatch)
    txt = f["consultar_obra"].executar({"obra": "casa 2"})
    assert "o que trava agora é habite-se" in txt and "esperando habite-se" in txt


def test_o_prompt_lembra_o_que_trava_as_casas_prontas(pool, conta):
    _casa(pool, conta, "Casa 1")
    _casa(pool, conta, "Casa 2", pronta=True)
    txt = ob.bloco_persona(pool, conta)
    assert "PENDÊNCIAS DAS CASAS" in txt and "Casa 2: trava em habite-se" in txt
    assert "Casa 1: trava" not in txt            # em obra: o que trava é a obra
    assert "marcar_documento" in txt and "andar_venda" in txt


# ── multi-tenant ──────────────────────────────────────────────────────────
def test_uma_conta_nao_mexe_na_casa_da_outra(pool, conta):
    o = _casa(pool, conta)
    with pool.connection() as c:
        outra = c.execute("insert into contas (tipo, nome) values ('pj','Outra') returning id"
                          ).fetchone()[0]
        c.commit()
    with pytest.raises(ValueError):
        ov.marcar_documento(pool, outra, o["id"], "habite_se")
    with pytest.raises(ValueError):
        ov.salvar_venda(pool, outra, o["id"], comprador="X")
