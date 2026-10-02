"""Contas a pagar: mudar o VENCIMENTO depois de lançada e anotar o MÊS DE
REFERÊNCIA (484, pedido do dono em 02/10/2026).

As decisões do dono que este arquivo trava:

* a referência é SÓ INFORMAÇÃO — aparece na lista, na edição e no relatório; o
  DRE não muda;
* em branco, vale o MÊS ANTERIOR AO VENCIMENTO (a conta que vence 10/11 é a de
  outubro) — calculado na leitura, sem reescrever as contas que já existiam.

E o cuidado do vencimento: só conta A PAGAR e ABERTA (a paga já tem a data no
caixa; a receber pode ser parcela de orçamento ou cobrança com link).
"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest

from finance import empresa as emp

# ═══════════════════════════════════════════════════════════ a regra pura
@pytest.mark.parametrize("venc,ref", [
    (date(2026, 11, 10), date(2026, 10, 1)),
    (date(2027, 1, 15), date(2026, 12, 1)),     # janeiro é a de dezembro do ano antes
    (date(2026, 10, 31), date(2026, 9, 1)),
    (None, None),
])
def test_o_padrao_e_o_mes_anterior_ao_vencimento(venc, ref):
    assert emp.referencia_padrao(venc) == ref


@pytest.mark.parametrize("texto,mes", [
    ("2026-10", date(2026, 10, 1)), ("2026-10-15", date(2026, 10, 1)),
    ("10/2026", date(2026, 10, 1)), (date(2026, 10, 9), date(2026, 10, 1)),
    ("", None), (None, None), ("outubro", None), ("2026-13", None),
])
def test_ler_mes(texto, mes):
    assert emp.ler_mes(texto) == mes


# ═══════════════════════════════════════════════════════════════ o banco
_MIGRACOES = ("018_chave_nfce_lancamentos.sql", "053_modulo_pj.sql",
              "057_natureza_lancamento.sql", "058_dados_empresa.sql",
              "064_clientes_lojista.sql", "066_pessoas_identidade.sql",
              "067_titulos_cliente.sql", "131_pessoa_cnpj.sql",
              "132_plano_contas_centros_custo.sql", "195_titulo_aprovacao.sql",
              "196_titulo_recorrencia.sql", "197_titulo_acrescimo.sql",
              "317_titulo_classificacao.sql", "325_tipo_despesa.sql",
              "484_titulo_vencimento_e_referencia.sql")

CONTA = 711


@pytest.fixture(scope="module")
def pool():
    from psycopg_pool import ConnectionPool

    from db.conexao import init_schema
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1,
                           open=True)
    dbname = "zaq_titulo_vencimento_referencia_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname} with (force)")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=4, open=True,
                       kwargs={"prepare_threshold": None})
    init_schema(p)
    base = Path(__file__).resolve().parent.parent / "db" / "migracoes"
    for m in _MIGRACOES:
        with p.connection() as c:
            c.execute((base / m).read_text(encoding="utf-8"))
            c.commit()
    from contas import equipe as _eq
    _eq.garantir_tabela(p)      # membros.email (listar_titulos lê)
    with p.connection() as c:
        c.execute("insert into contas (id, nome, tipo) values (%s,'Ref Teste','pj') "
                  "on conflict (id) do nothing", (CONTA,))
        c.commit()
    yield p
    p.close()


@pytest.fixture(autouse=True)
def _limpo(request):
    if "pool" not in request.fixturenames:
        yield
        return
    p = request.getfixturevalue("pool")
    with p.connection() as c:
        c.execute("delete from titulos where conta_id=%s", (CONTA,))
        c.execute("delete from lancamentos where conta_id=%s", (CONTA,))
        c.commit()
    yield


def _novo(pool, **kw):
    kw.setdefault("tipo", "pagar")
    kw.setdefault("descricao", "ALUGUEL DO PONTO")
    kw.setdefault("valor_centavos", 250000)
    kw.setdefault("vencimento", date(2026, 11, 10))
    tipo = kw.pop("tipo")
    return emp.criar_titulo(pool, CONTA, tipo, kw.pop("descricao"),
                            kw.pop("valor_centavos"), kw.pop("vencimento"), **kw)["id"]


def _um(pool, tid):
    for st in ("aberto", "pago", "cancelado"):
        for t in emp.listar_titulos(pool, CONTA, status=st):
            if t["id"] == tid:
                return t
    raise AssertionError(f"título {tid} sumiu")


def _manual(pool, tid) -> bool:
    with pool.connection() as c:
        return c.execute("select vencimento_manual from titulos where id=%s",
                         (tid,)).fetchone()[0]


def test_sem_referencia_vale_o_mes_anterior_ao_vencimento(pool):
    t = _um(pool, _novo(pool))
    assert t["referencia"] == date(2026, 10, 1) and not t["referencia_anotada"]


def test_a_referencia_anotada_ao_lancar(pool):
    t = _um(pool, _novo(pool, mes_referencia="2026-09"))
    assert t["referencia"] == date(2026, 9, 1) and t["referencia_anotada"]


def test_mudar_o_vencimento_da_conta_aberta(pool):
    tid = _novo(pool)
    assert not _manual(pool, tid)
    assert emp.editar_titulo(pool, CONTA, tid, vencimento=date(2026, 11, 20))
    t = _um(pool, tid)
    assert t["vencimento"] == date(2026, 11, 20)
    assert _manual(pool, tid), "a marca é o que faz a folha respeitar a data"


def test_regravar_a_mesma_data_nao_marca_como_mexida(pool):
    tid = _novo(pool)
    emp.editar_titulo(pool, CONTA, tid, vencimento=date(2026, 11, 10))
    assert not _manual(pool, tid)


def test_conta_paga_nao_muda_de_vencimento(pool):
    tid = _novo(pool)
    assert emp.dar_baixa_titulo(pool, CONTA, tid, data_pagto=date(2026, 11, 9))["ok"]
    emp.editar_titulo(pool, CONTA, tid, vencimento=date(2026, 12, 1))
    t = _um(pool, tid)
    assert t["vencimento"] == date(2026, 11, 10) and not _manual(pool, tid)


def test_conta_a_receber_nao_muda_de_vencimento_nem_referencia(pool):
    """Pode ser parcela de orçamento ou cobrança com link: mudar a data só aqui
    deixaria as duas pontas discordando."""
    tid = _novo(pool, tipo="receber", descricao="Parcela 1/2")
    emp.editar_titulo(pool, CONTA, tid, vencimento=date(2026, 12, 1),
                      mes_referencia="2026-05")
    with pool.connection() as c:
        r = c.execute("select vencimento, mes_referencia from titulos where id=%s",
                      (tid,)).fetchone()
    assert r == (date(2026, 11, 10), None)


def test_anotar_e_apagar_a_referencia(pool):
    tid = _novo(pool)
    emp.editar_titulo(pool, CONTA, tid, mes_referencia="2026-08")
    assert _um(pool, tid)["referencia"] == date(2026, 8, 1)
    emp.editar_titulo(pool, CONTA, tid, mes_referencia=None)
    t = _um(pool, tid)
    assert t["referencia"] == date(2026, 10, 1) and not t["referencia_anotada"], \
        "apagar volta ao padrão"
    # e editar outra coisa não mexe na referência
    emp.editar_titulo(pool, CONTA, tid, mes_referencia="2026-07")
    emp.editar_titulo(pool, CONTA, tid, descricao="ALUGUEL")
    assert _um(pool, tid)["referencia"] == date(2026, 7, 1)


def test_corrigir_a_data_nao_congela_a_referencia_velha(pool):
    """O formulário manda a referência preenchida. Corrigir só a data (10/11 →
    10/12) numa conta sem referência anotada: a referência segue a data nova."""
    tid = _novo(pool)
    emp.editar_titulo(pool, CONTA, tid, vencimento=date(2026, 12, 10),
                      mes_referencia="2026-10")     # o que veio preenchido
    t = _um(pool, tid)
    assert t["referencia"] == date(2026, 11, 1) and not t["referencia_anotada"]
    # mudar a referência DE VERDADE junto com a data, aí sim anota
    emp.editar_titulo(pool, CONTA, tid, vencimento=date(2027, 1, 10),
                      mes_referencia="2026-09")
    t = _um(pool, tid)
    assert t["referencia"] == date(2026, 9, 1) and t["referencia_anotada"]


def test_a_referencia_anda_com_a_conta_que_repete(pool):
    tid = _novo(pool, periodicidade="mensal", mes_referencia="2026-10")
    r = emp.dar_baixa_titulo(pool, CONTA, tid, data_pagto=date(2026, 11, 10))
    prox = _um(pool, r["proximo_titulo_id"])
    assert prox["vencimento"] == date(2026, 12, 10)
    assert prox["referencia"] == date(2026, 11, 1) and prox["referencia_anotada"]


def test_sem_referencia_anotada_a_proxima_segue_o_padrao(pool):
    tid = _novo(pool, periodicidade="mensal")
    r = emp.dar_baixa_titulo(pool, CONTA, tid, data_pagto=date(2026, 11, 10))
    prox = _um(pool, r["proximo_titulo_id"])
    assert prox["referencia"] == date(2026, 11, 1) and not prox["referencia_anotada"]


def test_a_conta_que_repete_segue_a_data_nova(pool):
    tid = _novo(pool, periodicidade="mensal")
    emp.editar_titulo(pool, CONTA, tid, vencimento=date(2026, 11, 15))
    r = emp.dar_baixa_titulo(pool, CONTA, tid, data_pagto=date(2026, 11, 15))
    assert _um(pool, r["proximo_titulo_id"])["vencimento"] == date(2026, 12, 15)


def test_o_relatorio_de_contas_a_pagar_mostra_a_referencia(pool):
    from web import painel_relatorios as rel
    _novo(pool)
    _novo(pool, descricao="LUZ", mes_referencia="2026-09")
    _novo(pool, tipo="receber", descricao="Honorário")
    d = rel._dados_titulos_abertos(pool, CONTA, "pagar")
    cols = [c["chave"] for c in d["colunas"]]
    assert cols.index("vencimento") < cols.index("referencia") < cols.index("descricao")
    assert sorted(r["referencia"] for r in d["linhas"]) == ["09/2026", "10/2026"]
    receber = rel._dados_titulos_abertos(pool, CONTA, "receber")
    assert "referencia" not in [c["chave"] for c in receber["colunas"]]


# ═════════════════════════════════════════════════════════════════ a tela
def test_a_tela_tem_os_campos():
    from web.portal import _env
    tpl = _env.loader.get_source(_env, "empresa")[0]
    # lançar: o mês de referência (some no "A receber")
    assert 'name="mes_referencia" id="tit-ref-input" type="month"' in tpl
    assert "ri.disabled = !pagar" in tpl
    # editar: a data só na conta a pagar aberta, a referência em toda a pagar
    assert ("{% if t.status == 'aberto' %}<input type=\"hidden\" name=\"tem_venc\" "
            "value=\"1\"><input type=\"date\" name=\"vencimento\"") in tpl
    assert '<input type="hidden" name="tem_ref" value="1"><input type="month" name="mes_referencia"' in tpl
    # a linha mostra a referência
    assert "ref. {{ t.referencia.strftime('%m/%Y') }}" in tpl
