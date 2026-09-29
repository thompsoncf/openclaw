"""O CSV do relatório do contador (finance/empresa.csv_contador) tem que abrir
com acento certo no Excel.

Relatado em produção em 29/09/2026: o arquivo baixado mostrava "ServiÃ§os" em
vez de "Serviços" — o Excel (inclusive PT-BR) abre um .csv de UTF-8 sem BOM
como se fosse ANSI/Latin-1, e todo acento vira dois caracteres errados. O CSV
já era UTF-8 de verdade (psycopg/Postgres devolvem str), só faltava o BOM que
avisa o Excel disso.

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
from finance import empresa as emp
from finance.livro_caixa import LivroCaixa
from finance.models import Lancamento, Tipo

_MIGRACOES = ("018_chave_nfce_lancamentos.sql",
              "053_modulo_pj.sql",
              "057_natureza_lancamento.sql",
              "132_plano_contas_centros_custo.sql",
              "143_plano_contas_locacao_buffet_servicos.sql")


@pytest.fixture(scope="module")
def pool():
    url = os.environ["TEST_DATABASE_URL"]  # garantido pela trava do conftest
    p = ConnectionPool(url, min_size=1, max_size=4, open=True,
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
            "insert into contas (tipo, nome) values ('pj', 'Teste CSV') returning id"
        ).fetchone()[0]
        c.commit()
    return cid


def test_o_csv_comeca_com_bom_pro_excel_detectar_utf8(pool, conta_id):
    hoje = date.today()
    csv = emp.csv_contador(pool, conta_id, hoje.year, hoje.month)
    assert csv.startswith("﻿")


def test_o_csv_preserva_acento_depois_de_decodificar_como_excel_faria(pool, conta_id):
    hoje = date.today()
    LivroCaixa(pool, conta_id).adicionar(
        Lancamento(tipo=Tipo.DESPESA, valor_centavos=15000, categoria="Serviços",
                  descricao="Diárias pedreiro — reforma", natureza="empresa"),
        forcar=True)

    csv = emp.csv_contador(pool, conta_id, hoje.year, hoje.month)
    # o mesmo caminho que o Excel percorre: bytes UTF-8 -> decodifica com BOM
    brutos = csv.encode("utf-8")
    de_volta = brutos.decode("utf-8-sig")
    assert "Serviços" in de_volta
    assert "Diárias pedreiro" in de_volta
    assert "Ã§" not in de_volta and "Ã¡" not in de_volta


def test_o_csv_tem_a_secao_de_titulos_mesmo_sem_nenhum_aberto(pool, conta_id):
    hoje = date.today()
    csv = emp.csv_contador(pool, conta_id, hoje.year, hoje.month)
    assert "TITULOS EM ABERTO" in csv
    assert "vencimento;tipo;descricao;contraparte;valor;atrasado" in csv
