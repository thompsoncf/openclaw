"""As obras da construtora (finance/obras.py, migração 351).

A primeira conta é a PX2 (conta 33): 13 despesas de obra em 25/09/2026 e nenhuma
dizendo de qual casa era. O desenho está em docs/mockups/nicho_construcao.html.

Os testes que mais importam:

`test_dividir_nao_mexe_no_valor_do_lancamento` — a nota das três casas continua
UM lançamento com o valor do comprovante; quebrar em três faria a mesma nota
mandada de novo não ser reconhecida como repetida.

`test_sem_obra_e_custo_de_obra_nao_toda_despesa` — DAS e contador não são de
casa nenhuma; se entrassem, o âmbar do "Sem obra" nunca apagaria.

`test_uma_conta_nao_ve_a_obra_da_outra` — multi-tenant.
"""
import os
from datetime import date
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from finance import empresa as emp
from finance import obras as ob
from finance.livro_caixa import LivroCaixa

_MIGRACOES = ("018_chave_nfce_lancamentos.sql", "053_modulo_pj.sql",
              "057_natureza_lancamento.sql", "132_plano_contas_centros_custo.sql",
              "143_plano_contas_locacao_buffet_servicos.sql", "186_plano_aporte_socios.sql",
              "336_plano_fardamentos.sql", "349_plano_obras.sql", "351_obras.sql")
_BASE = Path(__file__).resolve().parent.parent / "db" / "migracoes"


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_obras_test"
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


def _plano(pool, codigo):
    with pool.connection() as c:
        return c.execute("select id from plano_contas where codigo=%s", (codigo,)).fetchone()[0]


def _lanc(pool, conta, valor, *, tipo="despesa", categoria="Insumos", natureza="empresa",
          centro=None, plano=None, descricao="nota", quando=None):
    with pool.connection() as c:
        lid = c.execute(
            """insert into lancamentos (conta_id, tipo, valor_centavos, categoria, descricao,
                                        data, natureza, centro_custo_id, plano_conta_id)
                    values (%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
            (conta, tipo, valor, categoria, descricao, quando or date.today(), natureza,
             centro, plano)).fetchone()[0]
        c.commit()
    return lid


def _tres_casas(pool, conta):
    return [ob.criar_obra(pool, conta, f"Casa {n}", "casa", area_m2=45,
                          custo_previsto_centavos=8_200_000) for n in (1, 2, 3)]


# ── o cadastro ────────────────────────────────────────────────────────────
def test_a_obra_nasce_com_centro_de_custo_do_mesmo_nome(pool, conta):
    o = ob.criar_obra(pool, conta, "  Casa   1 ", "casa")
    assert o["nome"] == "Casa 1"
    with pool.connection() as c:
        nome, ativo = c.execute("select nome, ativo from centros_custo where id=%s",
                                (o["centro_custo_id"],)).fetchone()
    assert (nome, ativo) == ("Casa 1", True)


def test_a_casa_nasce_com_as_etapas_da_caixa_somando_100(pool, conta):
    o = ob.criar_obra(pool, conta, "Casa 1", "casa")
    assert [e["chave"] for e in o["etapas"]] == [ch for ch, *_ in ob.ETAPAS_CASA]
    assert sum(e["peso"] for e in o["etapas"]) == 100
    assert o["pct"] == 0 and o["status"] == "em_obra"


def test_a_reforma_nasce_com_as_etapas_de_reforma(pool, conta):
    o = ob.criar_obra(pool, conta, "Reforma Dona Maria", "reforma")
    assert [e["chave"] for e in o["etapas"]] == [ch for ch, *_ in ob.ETAPAS_REFORMA]
    assert sum(e["peso"] for e in o["etapas"]) == 100


def test_centro_que_ja_existia_com_o_mesmo_nome_vira_o_da_obra(pool, conta):
    with pool.connection() as c:
        cc = c.execute("insert into centros_custo (conta_id, nome) values (%s, 'casa 2') "
                       "returning id", (conta,)).fetchone()[0]
        c.commit()
    o = ob.criar_obra(pool, conta, "Casa 2", "casa")
    assert o["centro_custo_id"] == cc


@pytest.mark.parametrize("nome", ["", "   "])
def test_obra_sem_nome_nao_nasce(pool, conta, nome):
    with pytest.raises(ValueError, match="nome"):
        ob.criar_obra(pool, conta, nome)


def test_nome_repetido_nao_nasce(pool, conta):
    ob.criar_obra(pool, conta, "Casa 1")
    with pytest.raises(ValueError, match="Já existe"):
        ob.criar_obra(pool, conta, "CASA 1")


def test_a_obra_nova_copia_as_etapas_da_ultima_do_mesmo_tipo(pool, conta):
    """O ajuste feito na primeira casa vale pras próximas."""
    c1 = ob.criar_obra(pool, conta, "Casa 1")
    ob.salvar_etapas(pool, conta, c1["id"], [(None, "Fundação", 40), (None, "Resto", 60)])
    c2 = ob.criar_obra(pool, conta, "Casa 2")
    assert [(e["nome"], e["peso"]) for e in c2["etapas"]] == [("Fundação", 40), ("Resto", 60)]
    # e a reforma não herda da casa
    r = ob.criar_obra(pool, conta, "Reforma", "reforma")
    assert r["etapas"][0]["chave"] == ob.ETAPAS_REFORMA[0][0]


# ── o custo ───────────────────────────────────────────────────────────────
def test_material_mao_de_obra_e_outros(pool, conta):
    o = ob.criar_obra(pool, conta, "Casa 1", area_m2=40, custo_previsto_centavos=100_000)
    cc = o["centro_custo_id"]
    _lanc(pool, conta, 30_000, categoria="Insumos", centro=cc)
    _lanc(pool, conta, 10_000, categoria="Compras", centro=cc)          # histórico da PX2
    _lanc(pool, conta, 20_000, categoria="Servicos", centro=cc)
    _lanc(pool, conta, 5_000, categoria="Impostos", centro=cc)
    _lanc(pool, conta, 7_000, categoria="Outros", centro=cc, plano=_plano(pool, "3.1.04"))
    cu = ob.obter_obra(pool, conta, o["id"])["custos"]
    assert cu[ob.MATERIAL] == 40_000
    assert cu[ob.MAO_DE_OBRA] == 27_000                # a conta 3.1.04 manda na categoria
    assert cu[ob.OUTROS] == 5_000
    assert cu["total"] == 72_000


def test_previsto_e_custo_por_m2(pool, conta):
    o = ob.criar_obra(pool, conta, "Casa 1", area_m2=40, custo_previsto_centavos=100_000)
    _lanc(pool, conta, 50_000, centro=o["centro_custo_id"])
    o = ob.obter_obra(pool, conta, o["id"])
    assert o["pct_previsto"] == 50
    assert o["custo_m2"] == 1_250


def test_recebido_nao_e_custo(pool, conta):
    o = ob.criar_obra(pool, conta, "Reforma", "reforma")
    _lanc(pool, conta, 500_000, tipo="receita", categoria="Vendas", centro=o["centro_custo_id"])
    cu = ob.obter_obra(pool, conta, o["id"])["custos"]
    assert cu["recebido"] == 500_000 and cu["total"] == 0


def test_gasto_pessoal_nao_entra_na_obra(pool, conta):
    o = ob.criar_obra(pool, conta, "Casa 1")
    _lanc(pool, conta, 9_999, natureza="pessoal", centro=o["centro_custo_id"])
    assert ob.obter_obra(pool, conta, o["id"])["custos"]["total"] == 0


# ── dividir e pôr na obra ─────────────────────────────────────────────────
def test_dividir_nao_mexe_no_valor_do_lancamento(pool, conta):
    casas = _tres_casas(pool, conta)
    lid = _lanc(pool, conta, 176_391, descricao="Material - Sampaio")
    partes = ob.dividir(pool, conta, lid, [c["id"] for c in casas])
    assert [p["valor_centavos"] for p in partes] == [58_797, 58_797, 58_797]
    with pool.connection() as c:
        valor, centro = c.execute("select valor_centavos, centro_custo_id from lancamentos "
                                  "where id=%s", (lid,)).fetchone()
    assert (valor, centro) == (176_391, None)
    for casa in casas:
        o = ob.obter_obra(pool, conta, casa["id"])
        assert o["custos"]["total"] == 58_797
        assert o["lancamentos"][0]["dividido"] is True
        assert o["lancamentos"][0]["valor_inteiro_centavos"] == 176_391


def test_os_centavos_que_sobram_ficam_na_primeira(pool, conta):
    casas = _tres_casas(pool, conta)
    lid = _lanc(pool, conta, 100_001)
    partes = ob.dividir(pool, conta, lid, [c["id"] for c in casas])
    assert [p["valor_centavos"] for p in partes] == [33_335, 33_333, 33_333]
    assert sum(p["valor_centavos"] for p in partes) == 100_001


def test_dividir_de_novo_troca_a_divisao(pool, conta):
    casas = _tres_casas(pool, conta)
    lid = _lanc(pool, conta, 90_000)
    ob.dividir(pool, conta, lid, [c["id"] for c in casas])
    ob.dividir(pool, conta, lid, [casas[0]["id"], casas[1]["id"]])
    assert ob.obter_obra(pool, conta, casas[2]["id"])["custos"]["total"] == 0
    assert ob.obter_obra(pool, conta, casas[0]["id"])["custos"]["total"] == 45_000


def test_dividir_precisa_de_duas_obras(pool, conta):
    casas = _tres_casas(pool, conta)
    lid = _lanc(pool, conta, 1_000)
    with pytest.raises(ValueError, match="duas obras"):
        ob.dividir(pool, conta, lid, [casas[0]["id"], casas[0]["id"]])


def test_gasto_pessoal_nao_se_divide(pool, conta):
    casas = _tres_casas(pool, conta)
    lid = _lanc(pool, conta, 1_000, natureza="pessoal")
    with pytest.raises(ValueError, match="pessoal"):
        ob.dividir(pool, conta, lid, [c["id"] for c in casas])


def test_por_na_obra_desfaz_a_divisao(pool, conta):
    casas = _tres_casas(pool, conta)
    lid = _lanc(pool, conta, 90_000)
    ob.dividir(pool, conta, lid, [c["id"] for c in casas])
    ob.por_na_obra(pool, conta, lid, casas[1]["id"])
    assert ob.obter_obra(pool, conta, casas[1]["id"])["custos"]["total"] == 90_000
    assert ob.obter_obra(pool, conta, casas[0]["id"])["custos"]["total"] == 0


def test_sem_natureza_vira_empresa_ao_entrar_na_obra(pool, conta):
    o = ob.criar_obra(pool, conta, "Casa 1")
    lid = _lanc(pool, conta, 1_000, natureza=None)
    ob.por_na_obra(pool, conta, lid, o["id"])
    with pool.connection() as c:
        assert c.execute("select natureza from lancamentos where id=%s",
                         (lid,)).fetchone()[0] == "empresa"


def test_o_centro_escolhido_no_financeiro_desfaz_a_divisao(pool, conta):
    """Pôr o lançamento inteiro num centro pela tela do Financeiro vale mais que a
    divisão antiga — senão ela continuaria contando por baixo."""
    casas = _tres_casas(pool, conta)
    lid = _lanc(pool, conta, 90_000)
    ob.dividir(pool, conta, lid, [c["id"] for c in casas])
    assert LivroCaixa(pool, conta).definir_centro_custo(lid, casas[2]["centro_custo_id"])
    assert ob.obter_obra(pool, conta, casas[2]["id"])["custos"]["total"] == 90_000
    assert ob.obter_obra(pool, conta, casas[0]["id"])["custos"]["total"] == 0


def test_a_dre_por_centro_ve_a_parte_de_cada_obra(pool, conta):
    casas = _tres_casas(pool, conta)
    lid = _lanc(pool, conta, 90_000, plano=_plano(pool, "3.1.03"))
    ob.dividir(pool, conta, lid, [c["id"] for c in casas])
    hoje = date.today()
    d = emp.dre_por_centro(pool, conta, hoje.year, hoje.month)
    custos = next(li for li in d["linhas"] if "Custos" in li["nome"])
    for casa in casas:
        assert custos["por_centro"][casa["centro_custo_id"]] == -30_000
    assert "sem" not in custos["por_centro"]


# ── sem obra ──────────────────────────────────────────────────────────────
def test_sem_obra_e_custo_de_obra_nao_toda_despesa(pool, conta):
    _lanc(pool, conta, 1_000, categoria="Compras")
    _lanc(pool, conta, 2_000, categoria="Construcao")
    _lanc(pool, conta, 3_000, categoria="Servicos")
    _lanc(pool, conta, 4_000, categoria="Insumos")
    _lanc(pool, conta, 500, categoria="Outros", plano=_plano(pool, "3.1.04"))
    _lanc(pool, conta, 9_000, categoria="Impostos")            # DAS: não é de casa
    _lanc(pool, conta, 8_000, categoria="Compras", natureza="pessoal")
    _lanc(pool, conta, 7_000, tipo="receita", categoria="Vendas")
    f = ob.sem_obra(pool, conta)
    assert f["n"] == 5 and f["total_centavos"] == 10_500


def test_o_que_foi_pra_obra_sai_do_sem_obra(pool, conta):
    casas = _tres_casas(pool, conta)
    a = _lanc(pool, conta, 1_000)
    b = _lanc(pool, conta, 2_000)
    ob.por_na_obra(pool, conta, a, casas[0]["id"])
    ob.dividir(pool, conta, b, [c["id"] for c in casas])
    assert ob.sem_obra(pool, conta)["n"] == 0


# ── as etapas ─────────────────────────────────────────────────────────────
def test_telhado_e_a_cobertura(pool, conta):
    o = ob.criar_obra(pool, conta, "Casa 3")
    r = ob.marcar_etapa(pool, conta, o["id"], "terminou o telhado")
    assert r["etapa"] == "Cobertura" and r["pct"] == 12


@pytest.mark.parametrize("fala,etapa", [
    ("laje", "Estrutura"), ("reboco", "Reboco e revestimento"), ("piso", "Pisos"),
    ("portas e janelas", "Esquadrias"), ("pia e vaso", "Louças e metais"),
    ("Pintura", "Pintura"), ("fundação", "Preliminares e fundação"),
])
def test_o_jeito_de_falar_da_obra(pool, conta, fala, etapa):
    o = ob.criar_obra(pool, conta, "Casa 1")
    assert ob.marcar_etapa(pool, conta, o["id"], fala)["etapa"] == etapa


def test_etapa_que_nao_existe_diz_quais_existem(pool, conta):
    o = ob.criar_obra(pool, conta, "Casa 1")
    with pytest.raises(ValueError, match="Cobertura"):
        ob.marcar_etapa(pool, conta, o["id"], "piscina")


def test_todas_feitas_a_casa_fica_pronta_e_desmarcar_volta(pool, conta):
    o = ob.criar_obra(pool, conta, "Casa 2")
    for e in o["etapas"]:
        r = ob.marcar_etapa(pool, conta, o["id"], e["id"])
    assert r["pct"] == 100 and r["status"] == "pronta"
    assert ob.obter_obra(pool, conta, o["id"])["concluida_em"] == date.today()
    r = ob.marcar_etapa(pool, conta, o["id"], o["etapas"][0]["id"], concluida=False)
    assert r["status"] == "em_obra"
    assert ob.obter_obra(pool, conta, o["id"])["concluida_em"] is None


def test_salvar_etapas_guarda_a_data_de_quem_continua(pool, conta):
    o = ob.criar_obra(pool, conta, "Casa 1")
    ob.marcar_etapa(pool, conta, o["id"], "estrutura")
    linhas = [(e["chave"], e["nome"], e["peso"]) for e in o["etapas"] if e["chave"] != "pintura"]
    etapas = ob.salvar_etapas(pool, conta, o["id"], linhas + [(None, "Muro", 3)])
    por = {e["chave"]: e for e in etapas}
    assert por["estrutura"]["concluida_em"] == date.today()
    assert "pintura" not in por and por["muro"]["nome"] == "Muro"


# ── editar, arquivar, achar ───────────────────────────────────────────────
def test_renomear_a_obra_renomeia_o_centro(pool, conta):
    o = ob.criar_obra(pool, conta, "Casa 1")
    ob.editar_obra(pool, conta, o["id"], nome="Casa 1 - Qd 4")
    with pool.connection() as c:
        assert c.execute("select nome from centros_custo where id=%s",
                         (o["centro_custo_id"],)).fetchone()[0] == "Casa 1 - Qd 4"


def test_arquivar_tira_o_centro_das_listas_sem_apagar_nada(pool, conta):
    o = ob.criar_obra(pool, conta, "Casa 1")
    lid = _lanc(pool, conta, 1_000, centro=o["centro_custo_id"])
    ob.editar_obra(pool, conta, o["id"], status="arquivada")
    with pool.connection() as c:
        assert c.execute("select ativo from centros_custo where id=%s",
                         (o["centro_custo_id"],)).fetchone()[0] is False
        assert c.execute("select 1 from lancamentos where id=%s", (lid,)).fetchone()
    assert ob.listar_obras(pool, conta) == []
    assert len(ob.listar_obras(pool, conta, incluir_arquivadas=True)) == 1
    ob.editar_obra(pool, conta, o["id"], status="em_obra")
    with pool.connection() as c:
        assert c.execute("select ativo from centros_custo where id=%s",
                         (o["centro_custo_id"],)).fetchone()[0] is True


def test_achar_a_obra_pelo_jeito_que_ele_escreve(pool, conta):
    _tres_casas(pool, conta)
    ob.criar_obra(pool, conta, "Reforma da Dona Márcia", "reforma")
    assert ob.obra_por_nome(pool, conta, "casa 2")["nome"] == "Casa 2"
    assert ob.obra_por_nome(pool, conta, "reforma")["nome"] == "Reforma da Dona Márcia"
    assert ob.obra_por_nome(pool, conta, "dona marcia")["nome"] == "Reforma da Dona Márcia"
    assert ob.obra_por_nome(pool, conta, "casa") is None             # ambíguo
    assert ob.obra_por_nome(pool, conta, "galpão") is None


def test_uma_conta_nao_ve_a_obra_da_outra(pool, conta):
    o = ob.criar_obra(pool, conta, "Casa 1")
    with pool.connection() as c:
        outra = c.execute("insert into contas (tipo, nome) values ('pj','Outra') returning id"
                          ).fetchone()[0]
        c.commit()
    assert ob.obter_obra(pool, outra, o["id"]) is None
    assert ob.listar_obras(pool, outra) == []
    lid = _lanc(pool, outra, 1_000)
    with pytest.raises(ValueError):
        ob.por_na_obra(pool, outra, lid, o["id"])
    with pytest.raises(ValueError):
        ob.marcar_etapa(pool, outra, o["id"], "estrutura")


# ── o agente ──────────────────────────────────────────────────────────────
def test_o_bloco_do_agente_lista_as_obras_e_o_que_falta(pool, conta):
    _tres_casas(pool, conta)
    _lanc(pool, conta, 176_391, categoria="Compras")
    txt = ob.bloco_persona(pool, conta)
    assert "OBRAS ABERTAS" in txt and "Casa 1 (casa, 0%)" in txt
    assert "dividir_entre_obras" in txt and "consultar_obra" in txt and "marcar_etapa" in txt
    assert "SEM OBRA: 1 despesa(s)" in txt and "R$ 1.763,91" in txt
    assert ob.LINK_OBRAS in txt


def test_sem_obra_cadastrada_o_agente_manda_o_link(pool, conta):
    txt = ob.bloco_persona(pool, conta)
    assert "nenhuma cadastrada" in txt and ob.LINK_OBRAS in txt
    assert "OBRAS ABERTAS" not in txt


def test_o_resumo_de_uma_obra_no_whatsapp(pool, conta):
    o = ob.criar_obra(pool, conta, "Casa 2", area_m2=45, custo_previsto_centavos=8_200_000)
    _lanc(pool, conta, 4_610_000, categoria="Insumos", centro=o["centro_custo_id"])
    _lanc(pool, conta, 2_980_000, categoria="Servicos", centro=o["centro_custo_id"])
    txt = ob.resumo_da_obra(ob.obter_obra(pool, conta, o["id"]))
    assert txt.startswith("Casa 2: R$ 75.900,00 gastos")
    assert "material R$ 46.100,00" in txt and "mão de obra R$ 29.800,00" in txt
    assert "93%" in txt and "a próxima é preliminares e fundação" in txt
