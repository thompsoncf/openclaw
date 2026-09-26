"""O custo médio do m² por estado, do SINAPI/IBGE (finance/sinapi.py, migração 377).

`test_nao_compara_meia_obra_com_obra_inteira` — o gasto de uma obra pela metade
contra o custo médio de uma obra inteira diria "está barato" só porque não acabou.
"""
import os
from datetime import date
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from finance import obras as ob
from finance import sinapi
from finance import tools_pj

_MIGRACOES = ("018_chave_nfce_lancamentos.sql", "031_fornecedor_fase0.sql", "053_modulo_pj.sql",
              "057_natureza_lancamento.sql", "058_dados_empresa.sql",
              "132_plano_contas_centros_custo.sql", "349_plano_obras.sql", "351_obras.sql",
              "377_sinapi_referencia.sql")
_BASE = Path(__file__).resolve().parent.parent / "db" / "migracoes"

#: a resposta de verdade da API do SIDRA (tabela 2296, MA), como veio em 26/09/2026
RESPOSTA = [
    {"D2C": "Variável (Código)", "D3C": "Mês (Código)", "V": "Valor"},
    {"D2C": "48", "D3C": "202608", "V": "1969.07"},
    {"D2C": "2119", "D3C": "202608", "V": "1197.36"},
    {"D2C": "2120", "D3C": "202608", "V": "771.71"},
]


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_sinapi_test"
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


def test_le_a_resposta_do_ibge():
    assert sinapi.ler_resposta(RESPOSTA) == {"mes": date(2026, 8, 1), "total_centavos": 196907,
                                             "material_centavos": 119736,
                                             "mao_de_obra_centavos": 77171}
    assert sinapi.ler_resposta([RESPOSTA[0]]) is None
    assert sinapi.ler_resposta([]) is None


def test_busca_pelo_codigo_da_uf():
    urls = []
    ref = sinapi.buscar("pi", get=lambda url: urls.append(url) or RESPOSTA)
    assert "/n3/22/" in urls[0] and ref["total_centavos"] == 196907
    assert sinapi.buscar("XX", get=lambda url: RESPOSTA) is None


def test_a_semente_do_maranhao_e_o_mais_recente(pool):
    ref = sinapi.referencia(pool, "ma")
    assert ref["total_centavos"] == 196907 and ref["rotulo_mes"] == "ago/2026"
    sinapi.guardar(pool, "MA", {"mes": date(2026, 9, 1), "total_centavos": 197500,
                                "material_centavos": 120000, "mao_de_obra_centavos": 77500})
    assert sinapi.referencia(pool, "MA")["rotulo_mes"] == "set/2026"
    assert sinapi.referencia(pool, None) is None and sinapi.referencia(pool, "RR") is None


REF = {"uf": "MA", "total_centavos": 200000, "rotulo_mes": "ago/2026"}


def test_obra_pronta_compara_o_gasto():
    o = {"area_m2": 45.0, "pct": 100, "status": "pronta", "custo_m2": 170000,
         "custo_previsto_centavos": None}
    assert sinapi.comparar(o, REF) == {"base": "gasto", "valor": 170000, "pct": -15}


def test_nao_compara_meia_obra_com_obra_inteira():
    o = {"area_m2": 50.0, "pct": 40, "status": "em_obra", "custo_m2": 60000,
         "custo_previsto_centavos": 11_000_000}
    assert sinapi.comparar(o, REF) == {"base": "previsto", "valor": 220000, "pct": 10}
    sem_previsto = dict(o, custo_previsto_centavos=None)
    assert sinapi.comparar(sem_previsto, REF) == {"base": None, "valor": None, "pct": None}
    assert sinapi.comparar(dict(o, area_m2=None), REF) is None
    assert sinapi.comparar(o, None) is None


def test_atualiza_so_as_ufs_da_construcao_e_uma_vez_por_dia(pool):
    with pool.connection() as c:
        n = c.execute("select id from nichos where slug='construcao'").fetchone()
        if not n:
            n = c.execute("insert into nichos (slug, nome) values ('construcao','Construção') returning id"
                          ).fetchone()
        outro = c.execute("insert into nichos (slug, nome) values ('teste_sinapi','Outro') "
                          "on conflict do nothing returning id").fetchone()
        c.execute("insert into contas (tipo, nome, uf, nicho_id) values ('pj','PX2','PI',%s)", (n[0],))
        if outro:
            c.execute("insert into contas (tipo, nome, uf, nicho_id) values ('pj','Loja','SP',%s)",
                      (outro[0],))
        c.commit()
    chamadas = []
    get = lambda url: chamadas.append(url) or RESPOSTA  # noqa: E731
    assert sinapi.atualizar(pool, get=get) == 1
    assert [u for u in chamadas if "/n3/22/" in u] and not [u for u in chamadas if "/n3/35/" in u]
    assert sinapi.referencia(pool, "PI")["total_centavos"] == 196907
    chamadas.clear()
    assert sinapi.atualizar(pool, get=get) == 0 and not chamadas


def test_ibge_fora_do_ar_nao_derruba(pool):
    def quebra(url):
        raise RuntimeError("IBGE fora do ar")
    sinapi.atualizar(pool, hoje=date(2099, 1, 1), get=quebra)   # não levanta


def test_o_agente_cita_a_referencia(pool, monkeypatch):
    with pool.connection() as c:
        cid = c.execute("insert into contas (tipo, nome, uf) values ('pj','PX2','MA') returning id"
                        ).fetchone()[0]
        c.commit()
    ob.criar_obra(pool, cid, "Casa 1", "casa", area_m2=45, custo_previsto_centavos=8_100_000)
    monkeypatch.setattr(tools_pj, "_nicho_da_conta", lambda p, c: "construcao")
    f = {x.nome: x for x in tools_pj.construir_ferramentas_pj(pool, cid)}
    txt = f["consultar_obra"].executar({"obra": "casa 1"})
    assert "Referência SINAPI-MA" in txt and "o previsto dá R$ 1.800,00/m²" in txt
