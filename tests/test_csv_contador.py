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
from pathlib import Path

import pytest

from tests.relogio_fixo import HOJE

# O "hoje" é o de Brasília, com o processo parado às 23h (02h UTC do dia
# seguinte) — ver tests/relogio_fixo.py. O lançamento nasce no dia de Brasília
# (finance/relogio.py) e o teste pegava o mês com `date.today()`: no último dia
# do mês, das 21h à meia-noite, eram meses diferentes (CI do #917, 01/10/2026).
pytestmark = pytest.mark.usefixtures("servidor_as_23h")
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from finance import empresa as emp
from finance.livro_caixa import LivroCaixa
from finance.models import Lancamento, Tipo

_MIGRACOES = ("018_chave_nfce_lancamentos.sql",
              "053_modulo_pj.sql",
              "057_natureza_lancamento.sql",
              "058_dados_empresa.sql",
              "064_clientes_lojista.sql",
              "066_pessoas_identidade.sql",
              "067_titulos_cliente.sql",
              "072_membro_login_web.sql",
              "131_pessoa_cnpj.sql",
              "132_plano_contas_centros_custo.sql",
              "143_plano_contas_locacao_buffet_servicos.sql",
              "162_titulo_parcela_do_orcamento.sql",
              "195_titulo_aprovacao.sql",
              "196_titulo_recorrencia.sql",
              "197_titulo_acrescimo.sql",
              "317_titulo_classificacao.sql",
              "323_titulo_ajustes.sql")


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
            """insert into contas (tipo, nome, nome_fantasia, documento)
                    values ('pj', 'Teste CSV', 'Padaria do Zé', '12345678000199')
               returning id"""
        ).fetchone()[0]
        c.commit()
    return cid


def test_o_csv_comeca_com_bom_pro_excel_detectar_utf8(pool, conta_id):
    hoje = HOJE
    csv = emp.csv_contador(pool, conta_id, hoje.year, hoje.month)
    assert csv.startswith("﻿")


def test_o_csv_preserva_acento_depois_de_decodificar_como_excel_faria(pool, conta_id):
    hoje = HOJE
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
    hoje = HOJE
    csv = emp.csv_contador(pool, conta_id, hoje.year, hoje.month)
    assert "TITULOS EM ABERTO" in csv
    assert "vencimento;tipo;descricao;contraparte;valor;atrasado" in csv


# ---------------------------------------------- cabeçalho (empresa/CNPJ/período)

def test_o_cabecalho_traz_empresa_cnpj_e_periodo(pool, conta_id):
    csv = emp.csv_contador(pool, conta_id, 2026, 9)
    linhas = csv.lstrip("﻿").split("\n")
    assert linhas[0] == "Empresa;Padaria do Zé"
    assert linhas[1] == "CNPJ;12.345.678/0001-99"
    assert linhas[2] == "Periodo;09/2026"
    assert linhas[3] == ""
    assert linhas[4] == (
        "data;tipo;categoria;plano_conta_codigo;descricao;valor;origem;natureza")


def test_o_cabecalho_pula_o_cnpj_quando_a_conta_nao_tem_documento(pool):
    with pool.connection() as c:
        cid = c.execute(
            "insert into contas (tipo, nome) values ('pj', 'Sem Documento') returning id"
        ).fetchone()[0]
        c.commit()
    hoje = HOJE
    csv = emp.csv_contador(pool, cid, hoje.year, hoje.month)
    linhas = csv.lstrip("﻿").split("\n")
    assert linhas[0] == "Empresa;Sem Documento"
    assert linhas[1] == "Periodo;%02d/%d" % (hoje.month, hoje.year)
    assert not any(l.startswith("CNPJ;") for l in linhas)


# --------------------------------------- código do plano de contas por lançamento

def test_o_lancamento_leva_o_codigo_do_plano_de_contas(pool, conta_id):
    from finance import plano_contas as pc
    ids = {c["codigo"]: c["id"] for c in pc.listar_plano(pool)}
    hoje = HOJE
    LivroCaixa(pool, conta_id).adicionar(
        Lancamento(tipo=Tipo.RECEITA, valor_centavos=50000, categoria="Vendas",
                  descricao="Venda classificada", natureza="empresa",
                  plano_conta_id=ids["1.1.02"]), forcar=True)

    csv = emp.csv_contador(pool, conta_id, hoje.year, hoje.month)
    linha = next(l for l in csv.split("\n") if "Venda classificada" in l)
    campos = linha.split(";")
    assert campos[3] == "1.1.02"


def test_o_lancamento_sem_plano_de_contas_leva_o_codigo_em_branco(pool, conta_id):
    hoje = HOJE
    LivroCaixa(pool, conta_id).adicionar(
        Lancamento(tipo=Tipo.DESPESA, valor_centavos=8000, categoria="Outros",
                  descricao="Sem plano ainda", natureza="empresa"), forcar=True)

    csv = emp.csv_contador(pool, conta_id, hoje.year, hoje.month)
    linha = next(l for l in csv.split("\n") if "Sem plano ainda" in l)
    campos = linha.split(";")
    assert campos[3] == ""
