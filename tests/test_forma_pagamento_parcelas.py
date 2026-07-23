"""Forma de pagamento canonica + previsao de fatura do cartao (parcelado).

Cobre os dois ajustes do lancamento no zaq:
  1. identificar a forma de pagamento (pix/debito/credito/especie/...);
  2. compra parcelada no credito -> 1a parcela vira despesa e as futuras viram
     previsao da fatura (modelo fatura: fora do saldo ate' vencerem).
"""
import os
from datetime import date
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from finance.livro_caixa import LivroCaixa, _somar_meses
from finance.models import Lancamento, Tipo, canonizar_forma_pagamento

_MIGRACOES = ("018_chave_nfce_lancamentos.sql",
              "057_natureza_lancamento.sql",
              "088_forma_pagamento_parcelas.sql")


# ── parte pura (sem banco): canonizacao da forma de pagamento ────────────────
@pytest.mark.parametrize("entrada,esperado", [
    ("no crédito", "credito"),
    ("paguei no credito", "credito"),
    ("cartão de crédito", "credito"),
    ("no débito", "debito"),
    ("cartão", "debito"),          # cartao solto -> debito (a vista, o comum)
    ("mandei um pix", "pix"),
    ("PIX", "pix"),
    ("dinheiro", "especie"),
    ("em espécie", "especie"),
    ("boleto", "boleto"),
    ("transferência", "transferencia"),
    ("ted", "transferencia"),
    ("", ""),                       # vazio fica vazio (nao 'outro')
    (None, ""),
    ("cheque", "outro"),            # desconhecido -> outro
])
def test_canonizar_forma_pagamento(entrada, esperado):
    assert canonizar_forma_pagamento(entrada) == esperado


def test_somar_meses_vira_primeiro_dia():
    assert _somar_meses(date(2026, 3, 15), 0) == date(2026, 3, 1)
    assert _somar_meses(date(2026, 3, 15), 2) == date(2026, 5, 1)
    assert _somar_meses(date(2026, 11, 20), 3) == date(2027, 2, 1)  # vira o ano


# ── parte com banco ──────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def pool():
    test_db_url = os.environ["TEST_DATABASE_URL"]  # garantido pela trava do conftest
    p = ConnectionPool(test_db_url, min_size=1, max_size=4, open=True,
                       kwargs={"prepare_threshold": None})
    init_schema(p)  # idempotente; ja' cria forma_pagamento e parcelas_cartao
    base = Path(__file__).resolve().parent.parent / "db" / "migracoes"
    for m in _MIGRACOES:  # roda a migracao tambem (garante que o SQL e' valido)
        with p.connection() as c:
            c.execute((base / m).read_text(encoding="utf-8"))
            c.commit()
    yield p
    p.close()


@pytest.fixture()
def conta_id(pool):
    with pool.connection() as c:
        cid = c.execute(
            "insert into contas (tipo, nome) values ('pf', 'Teste Cartao') returning id"
        ).fetchone()[0]
        c.commit()
    return cid


def test_forma_pagamento_persiste(pool, conta_id):
    liv = LivroCaixa(pool, conta_id)
    salvo = liv.adicionar(
        Lancamento.criar(tipo=Tipo.DESPESA, valor_reais=50, categoria="Mercado",
                         descricao="feira", forma_pagamento="mandei um pix"),
        forcar=True,
    )
    achado = next(l for l in liv.listar() if l.id == salvo.id)
    assert achado.forma_pagamento == "pix"


def test_compra_parcelada_so_a_primeira_no_saldo(pool, conta_id):
    liv = LivroCaixa(pool, conta_id)
    saldo0 = liv.saldo_centavos()
    # R$ 1200 em 12x, hoje
    r = liv.registrar_compra_parcelada(
        valor_total_centavos=120000, parcelas=12, categoria="Compras",
        descricao="Geladeira", data_compra=date.today(),
    )
    # o saldo caiu SO' a 1a parcela (R$100), nao os R$1200
    assert liv.saldo_centavos() == saldo0 - 10000
    assert r["total_centavos"] == 120000
    assert r["parcelas"] == 12

    # previsao: as 11 futuras, uma por mes (nao inclui a do mes atual, ja' lancada)
    prev = liv.previsao_cartao(meses=12)
    assert prev["total_centavos"] == 110000  # 11 x R$100
    assert len(prev["pontos"]) == 11
    assert all(p["parcelas"] == 1 for p in prev["pontos"])


def test_parcelas_com_resto_somam_o_total(pool, conta_id):
    liv = LivroCaixa(pool, conta_id)
    # R$ 100,00 em 3x = 33,34 + 33,33 + 33,33 (resto de 1 centavo na 1a)
    r = liv.registrar_compra_parcelada(
        valor_total_centavos=10000, parcelas=3, categoria="Outros",
        descricao="Curso", data_compra=date.today(),
    )
    assert r["valor_parcela_centavos"] == 3334          # 1a leva o resto
    assert r["valor_parcela_regular_centavos"] == 3333  # regulares
    prev = liv.previsao_cartao(meses=6)
    # total lancado (1a) + previsto (2a+3a) == total exato, sem centavo perdido
    lancado_1a = 3334
    assert lancado_1a + prev["total_centavos"] == 10000


def test_materializar_promove_parcela_vencida(pool, conta_id):
    liv = LivroCaixa(pool, conta_id)
    saldo0 = liv.saldo_centavos()
    # compra "antiga" (mes passado) em 3x: 1a ja' foi no mes passado; a deste mes
    # esta' vencida (competencia <= mes atual) e deve virar despesa ao materializar
    compra = _somar_meses(date.today(), -1).replace(day=10)  # ~mes passado, dia 10
    liv.registrar_compra_parcelada(
        valor_total_centavos=30000, parcelas=3, categoria="Compras",
        descricao="Sofa", data_compra=compra,
    )
    # apos o registro: a 1a (mes passado) ja' e' despesa; a do mes atual ainda
    # esta' 'previsto'. saldo caiu so' a 1a (R$100).
    assert liv.saldo_centavos() == saldo0 - 10000

    n = liv.materializar_parcelas_devidas(hoje=date.today())
    assert n == 1  # a parcela do mes atual venceu
    # agora o saldo caiu 2 parcelas (mes passado + mes atual)
    assert liv.saldo_centavos() == saldo0 - 20000

    # idempotente: rodar de novo nao lanca nada
    assert liv.materializar_parcelas_devidas(hoje=date.today()) == 0
    assert liv.saldo_centavos() == saldo0 - 20000

    # sobra 1 parcela no futuro na previsao
    prev = liv.previsao_cartao(meses=6)
    assert prev["total_centavos"] == 10000
    assert len(prev["pontos"]) == 1
