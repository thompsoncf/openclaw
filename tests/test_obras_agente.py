"""As ferramentas de obra do agente do WhatsApp (finance/tools_pj.py).

As quatro conversas do desenho (docs/mockups/nicho_construcao.html, seção 07):
a nota que é das três casas, a mão de obra da etapa, "quanto já gastei na casa
2?" e "terminou o telhado da casa 3". E a regra que vale pra todas: as
ferramentas de obra só existem na conta de construção (§6 do CLAUDE.md).
"""
import os
from datetime import date
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from finance import obras as ob
from finance import tools_pj

_MIGRACOES = ("018_chave_nfce_lancamentos.sql", "053_modulo_pj.sql",
              "057_natureza_lancamento.sql", "132_plano_contas_centros_custo.sql",
              "349_plano_obras.sql", "351_obras.sql")
_BASE = Path(__file__).resolve().parent.parent / "db" / "migracoes"
_OBRA = {"consultar_obra", "dividir_entre_obras", "por_na_obra", "marcar_etapa", "cobrar_parcela", "guardar_foto_da_obra",
         "gastos_sem_obra"}


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_obras_agente_test"
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


def _ferramentas(pool, conta, monkeypatch, nicho="construcao") -> dict:
    monkeypatch.setattr(tools_pj, "_nicho_da_conta", lambda p, c: nicho)
    return {f.nome: f for f in tools_pj.construir_ferramentas_pj(pool, conta)}


def _lanc(pool, conta, valor, categoria="Compras", centro=None):
    with pool.connection() as c:
        lid = c.execute(
            """insert into lancamentos (conta_id, tipo, valor_centavos, categoria, descricao,
                                        data, natureza, centro_custo_id)
                    values (%s,'despesa',%s,%s,'Material - Sampaio',%s,'empresa',%s)
                 returning id""", (conta, valor, categoria, date.today(), centro)).fetchone()[0]
        c.commit()
    return lid


# ── só na construção ──────────────────────────────────────────────────────
def test_a_construtora_recebe_as_ferramentas_de_obra(pool, conta, monkeypatch):
    assert _OBRA <= set(_ferramentas(pool, conta, monkeypatch))


@pytest.mark.parametrize("nicho", ["clinica", "eventos", "arquitetura", ""])
def test_ninguem_mais_recebe(pool, conta, monkeypatch, nicho):
    assert not _OBRA & set(_ferramentas(pool, conta, monkeypatch, nicho))


def test_nenhuma_ferramenta_cria_obra(pool, conta, monkeypatch):
    """Obra nasce no painel (decisão 1 do dono): criar obra cria centro de custo."""
    assert not any("criar" in n and "obra" in n for n in _ferramentas(pool, conta, monkeypatch))


# ── as conversas ──────────────────────────────────────────────────────────
def test_a_nota_das_tres_casas(pool, conta, monkeypatch):
    for n in (1, 2, 3):
        ob.criar_obra(pool, conta, f"Casa {n}")
    lid = _lanc(pool, conta, 176_391)
    f = _ferramentas(pool, conta, monkeypatch)
    txt = f["dividir_entre_obras"].executar({"lancamento_id": lid, "todas": True})
    assert txt.count("R$ 587,97") == 3 and "Casa 3" in txt


def test_dividir_so_entre_as_que_ele_disse(pool, conta, monkeypatch):
    for n in (1, 2, 3):
        ob.criar_obra(pool, conta, f"Casa {n}")
    lid = _lanc(pool, conta, 100_000)
    f = _ferramentas(pool, conta, monkeypatch)
    txt = f["dividir_entre_obras"].executar({"lancamento_id": lid, "obras": ["casa 1", "casa 3"]})
    assert "R$ 500,00 em Casa 1" in txt and "R$ 500,00 em Casa 3" in txt and "Casa 2" not in txt


def test_obra_que_nao_existe_pergunta_qual(pool, conta, monkeypatch):
    ob.criar_obra(pool, conta, "Casa 1")
    ob.criar_obra(pool, conta, "Casa 2")
    lid = _lanc(pool, conta, 1_000)
    f = _ferramentas(pool, conta, monkeypatch)
    txt = f["por_na_obra"].executar({"lancamento_id": lid, "obra": "galpão"})
    assert "Não achei" in txt and "Casa 1, Casa 2" in txt


def test_sem_obra_nenhuma_manda_o_link(pool, conta, monkeypatch):
    f = _ferramentas(pool, conta, monkeypatch)
    assert ob.LINK_OBRAS in f["consultar_obra"].executar({"obra": "casa 1"})
    assert ob.LINK_OBRAS in f["consultar_obra"].executar({})


def test_quanto_ja_gastei_na_casa_2(pool, conta, monkeypatch):
    o = ob.criar_obra(pool, conta, "Casa 2", custo_previsto_centavos=8_200_000)
    _lanc(pool, conta, 4_610_000, "Insumos", o["centro_custo_id"])
    f = _ferramentas(pool, conta, monkeypatch)
    txt = f["consultar_obra"].executar({"obra": "casa 2"})
    assert txt.startswith("Casa 2: R$ 46.100,00 gastos") and "56%" in txt


def test_terminou_o_telhado_da_casa_3(pool, conta, monkeypatch):
    ob.criar_obra(pool, conta, "Casa 3")
    f = _ferramentas(pool, conta, monkeypatch)
    txt = f["marcar_etapa"].executar({"obra": "Casa 3", "etapa": "telhado"})
    assert txt == "Cobertura concluída em Casa 3: a obra está em 12%."


def test_distribuir_os_gastos_sem_obra_um_por_um(pool, conta, monkeypatch):
    ob.criar_obra(pool, conta, "Casa 1")
    lid = _lanc(pool, conta, 88_349)
    f = _ferramentas(pool, conta, monkeypatch)
    lista = f["gastos_sem_obra"].executar({})
    assert f"id={lid}" in lista and "R$ 883,49" in lista
    assert f["por_na_obra"].executar({"lancamento_id": lid, "obra": "casa 1"}) == \
        "Pus R$ 883,49 na Casa 1. ✅"
    assert f["gastos_sem_obra"].executar({}) == "Nenhuma despesa de obra sem obra. ✅"


def test_o_prompt_da_construtora_fala_das_obras_dela(pool, conta, monkeypatch):
    ob.criar_obra(pool, conta, "Casa 1")
    monkeypatch.setattr(tools_pj, "_nicho_da_conta", lambda p, c: "construcao")
    txt = tools_pj.bloco_persona_pj(pool, conta, "PX2")
    assert "CONSTRUÇÃO E REFORMA" in txt and "OBRAS ABERTAS" in txt and "Casa 1" in txt


def test_o_prompt_de_outro_ramo_nao_fala_de_obra(pool, conta, monkeypatch):
    ob.criar_obra(pool, conta, "Casa 1")
    monkeypatch.setattr(tools_pj, "_nicho_da_conta", lambda p, c: "clinica")
    assert "OBRAS ABERTAS" not in tools_pj.bloco_persona_pj(pool, conta, "Clínica")
