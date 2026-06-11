"""Testes de integracao contra um Postgres real.

Exige DATABASE_URL apontando pra um banco de teste (as tabelas sao limpas
no inicio de cada teste). Rode com: pytest -q
"""
import os
from datetime import date

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from usuarios import usuarios as u
from finance.livro_caixa import LivroCaixa
from finance.models import (
    Lancamento, Tipo, normalizar_categoria, reais_para_centavos, formatar_brl,
)


@pytest.fixture()
def pool():
    url = os.environ["DATABASE_URL"]
    p = ConnectionPool(url, min_size=1, max_size=4, open=True)
    init_schema(p)
    with p.connection() as c:
        c.execute("truncate usuarios, lancamentos, uso_diario restart identity cascade")
        c.commit()
    yield p
    p.close()


def test_normalizar_categoria():
    assert normalizar_categoria(Tipo.DESPESA, "mercado") == "Mercado"
    assert normalizar_categoria(Tipo.DESPESA, "MERCADO") == "Mercado"
    assert normalizar_categoria(Tipo.DESPESA, "xpto") == "Outros"
    assert normalizar_categoria(Tipo.RECEITA, "salário") == "Salário"


def test_centavos_sem_erro_de_float():
    assert reais_para_centavos("0.10") + reais_para_centavos("0.20") == 30
    assert reais_para_centavos(87.40) == 8740
    assert formatar_brl(8740) == "R$ 87,40"
    with pytest.raises(ValueError):
        reais_para_centavos("-5")


def test_usuario_get_or_create_idempotente(pool):
    a = u.get_or_create(pool, telegram_id=111, nome="Ana")
    b = u.get_or_create(pool, telegram_id=111)
    assert a.id == b.id
    assert a.nome == "Ana"


def test_saldo_e_isolamento_entre_usuarios(pool):
    ana = u.get_or_create(pool, telegram_id=1, nome="Ana")
    bob = u.get_or_create(pool, telegram_id=2, nome="Bob")
    livro_ana = LivroCaixa(pool, ana.id)
    livro_bob = LivroCaixa(pool, bob.id)

    livro_ana.adicionar(Lancamento.criar(Tipo.RECEITA, 1000, "Salário"))
    livro_ana.adicionar(Lancamento.criar(Tipo.DESPESA, 87.40, "Mercado"))
    livro_bob.adicionar(Lancamento.criar(Tipo.DESPESA, 50, "Lazer"))

    # Ana: 1000 - 87.40 = 912.60
    assert livro_ana.saldo_centavos() == 91260
    # Bob nao enxerga nada da Ana: so' -50
    assert livro_bob.saldo_centavos() == -5000
    assert len(livro_ana.listar()) == 2
    assert len(livro_bob.listar()) == 1


def test_relatorio_por_categoria(pool):
    ana = u.get_or_create(pool, telegram_id=9, nome="Ana")
    livro = LivroCaixa(pool, ana.id)
    hoje = date.today()
    livro.adicionar(Lancamento.criar(Tipo.DESPESA, 100, "Mercado", data=hoje))
    livro.adicionar(Lancamento.criar(Tipo.DESPESA, 40, "Mercado", data=hoje))
    livro.adicionar(Lancamento.criar(Tipo.DESPESA, 30, "Transporte", data=hoje))
    rel = livro.total_por_categoria(Tipo.DESPESA, mes=hoje.month, ano=hoje.year)
    assert rel["Mercado"] == 14000
    assert rel["Transporte"] == 3000


def test_limite_de_uso_diario(pool):
    ana = u.get_or_create(pool, telegram_id=7, nome="Ana")
    ana.limite_mensagens_dia = 3
    liberados = 0
    for _ in range(5):
        ok, _restante = u.checar_e_registrar_uso(pool, ana)
        if ok:
            liberados += 1
    assert liberados == 3  # bateu o teto e travou
