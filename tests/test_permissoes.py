"""Testa que cada papel (dono/membro/restrito) tem acesso APENAS as ferramentas permitidas."""
import os

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from finance.livro_caixa import LivroCaixa
from finance.lista_compras import ListaCompras
from finance.tools import construir_ferramentas


@pytest.fixture()
def pool():
    p = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=4, open=True)
    init_schema(p)
    with p.connection() as c:
        c.execute("truncate lancamentos, membros, contas restart identity cascade")
        c.commit()
    yield p
    p.close()


def test_ferramentas_por_papel(pool):
    # Setup: cria uma conta e livro/lista
    with pool.connection() as c:
        c.execute(
            "insert into contas (id, tipo, nome, email, plano, status, limite_mensagens_dia) "
            "values (1, 'pf', 'Test', 'test@ex.com', 'teste', 'trial', 1000)"
        )
        c.commit()
    livro = LivroCaixa(pool, 1)
    lista = ListaCompras(pool, 1, membro_id=None)

    # DONO: acesso completo a financas + lista
    fd = {f.nome for f in construir_ferramentas(livro, lista, "dono")}
    assert "lancar_despesa" in fd
    assert "lancar_receita" in fd
    assert "adicionar_lista_compras" in fd
    assert "ver_lista_compras" in fd
    assert "marcar_comprado_lista" in fd
    assert "remover_lista_compras" in fd
    assert "comparar_precos_lista" in fd

    # MEMBRO: acesso a financas + lista
    fm = {f.nome for f in construir_ferramentas(livro, lista, "membro")}
    assert "lancar_despesa" in fm
    assert "lancar_receita" in fm
    assert "adicionar_lista_compras" in fm
    assert "ver_lista_compras" in fm
    assert "marcar_comprado_lista" in fm
    assert "remover_lista_compras" in fm
    assert "comparar_precos_lista" in fm

    # RESTRITO: SO' lista (inclui comparar precos e remover), NENHUMA financeira
    fr = {f.nome for f in construir_ferramentas(livro, lista, "restrito")}
    assert fr == {"adicionar_lista_compras", "ver_lista_compras",
                  "marcar_comprado_lista", "remover_lista_compras",
                  "avisar_lista_pronta", "comparar_precos_lista"}
    assert "lancar_despesa" not in fr
    assert "lancar_receita" not in fr
    assert "ver_saldo" not in fr
    assert "relatorio_mes" not in fr
