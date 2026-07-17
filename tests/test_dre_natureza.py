"""Regressão do dre_mes: filtro estrito + visibilidade do "a definir".

O DRE agrega SÓ natureza='empresa' (resultado do negócio). Os lançamentos ainda
não classificados (natureza null, "a definir") NÃO entram no resultado — podem ser
gasto pessoal — mas o dre_mes passa a devolver quanto ficou de fora
(a_definir_n / a_definir_centavos) pra tela avisar e o número nunca sair magro
silenciosamente.

Roda com banco de TESTE separado (ver tests/conftest.py):
    export TEST_DATABASE_URL="postgresql://.../banco_de_teste"
    pytest
"""
import os
from datetime import date
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from finance import empresa
from finance.livro_caixa import LivroCaixa
from finance.models import Lancamento, Tipo

_MIGRACOES = ("018_chave_nfce_lancamentos.sql",
              "053_modulo_pj.sql",
              "057_natureza_lancamento.sql")


@pytest.fixture(scope="module")
def pool():
    test_db_url = os.environ["TEST_DATABASE_URL"]  # garantido pela trava do conftest
    p = ConnectionPool(test_db_url, min_size=1, max_size=4, open=True,
                       kwargs={"prepare_threshold": None})
    init_schema(p)  # idempotente
    base = Path(__file__).resolve().parent.parent / "db" / "migracoes"
    for m in _MIGRACOES:
        with p.connection() as c:
            c.execute((base / m).read_text(encoding="utf-8"))
            c.commit()
    yield p
    p.close()


@pytest.fixture()
def conta_id(pool):
    with pool.connection() as c:
        cid = c.execute(
            "insert into contas (tipo, nome) values ('pj', 'Teste DRE') returning id"
        ).fetchone()[0]
        c.commit()
    return cid


def test_dre_exclui_a_definir_mas_expoe_o_que_ficou_de_fora(pool, conta_id):
    hoje = date.today()
    liv = LivroCaixa(pool, conta_id)
    # 1 receita classificada como empresa: ENTRA no DRE.
    liv.adicionar(Lancamento(tipo=Tipo.RECEITA, valor_centavos=100000,
                             categoria="Vendas", descricao="Venda PJ",
                             data=hoje, natureza="empresa"), forcar=True)
    # 1 despesa "a definir" (natureza null): FICA DE FORA do resultado.
    liv.adicionar(Lancamento(tipo=Tipo.DESPESA, valor_centavos=25000,
                             categoria="Outros", descricao="Sem classificar",
                             data=hoje), forcar=True)  # natureza null

    dre = empresa.dre_mes(pool, conta_id, hoje.year, hoje.month)

    # (a) o null NÃO entrou no resultado
    assert dre["receitas_centavos"] == 100000
    assert dre["despesas_centavos"] == 0
    assert dre["resultado_centavos"] == 100000

    # (b) mas o que ficou de fora está visível
    assert dre["a_definir_n"] == 1
    assert dre["a_definir_centavos"] == 25000
