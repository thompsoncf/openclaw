"""Uma conexão por requisição (20/09/2026).

Medido no celular do dono, com a tela de Velocidade: a Fila abria **16 conexões**
pra desenhar uma tela. Cada conexão entregue paga um `SELECT 1` de verificação, e
com o serviço em Oregon e o banco em us-east-1 essa verificação custa os mesmos
~100 ms de qualquer consulta — ~1,6 s dos 4,0 s de banco, gastos sem desenhar
nada.

O que se prova aqui não é a economia (essa está na Velocidade), é a SEGURANÇA da
troca: que cada bloco continua confirmando o próprio trabalho, que um bloco que
falha não leva junto o que já tinha sido salvo, e que fora de requisição nada
mudou — o poller, os crons e a própria suíte seguem no caminho de antes.
"""
import os

import pytest
from psycopg_pool import ConnectionPool

from db import conexao as cx
from db import medicao


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_conexao_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = cx._PoolComConta(url, connection_class=medicao.ConexaoMedida, min_size=1, max_size=4,
                         open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute("create table marca (id bigserial primary key, texto text)")
        c.commit()
    yield p
    p.close()


def _marcas(pool):
    with pool.connection() as c:
        return [t for (t,) in c.execute("select texto from marca order by id").fetchall()]


def test_a_requisicao_usa_uma_conexao_so(pool):
    """Três blocos, uma conexão — e o pool entrega uma vez só."""
    token = cx.abrir_requisicao()
    med = medicao.abrir()
    try:
        vistos = []
        for _ in range(3):
            with pool.connection() as c:
                vistos.append(id(c))
                c.execute("select 1")
    finally:
        m = medicao.fechar(med)
        cx.fechar_requisicao(token)

    assert len(set(vistos)) == 1, "cada bloco abriu a sua conexão de novo"
    assert m["conexoes"] == 1, f"o pool entregou {m['conexoes']} conexões"


def test_cada_bloco_confirma_o_proprio_trabalho(pool):
    """O ponto delicado da troca. Antes, cada bloco era uma conexão e confirmava
    ao sair; se o reaproveitamento adiasse a confirmação pro fim da requisição,
    uma falha no fim desfaria o que já estava salvo no começo — e isso quebraria
    em silêncio, só no dia ruim."""
    token = cx.abrir_requisicao()
    try:
        with pool.connection() as c:
            c.execute("insert into marca (texto) values ('primeiro')")
        # o segundo bloco morre no meio
        with pytest.raises(ZeroDivisionError):
            with pool.connection() as c:
                c.execute("insert into marca (texto) values ('segundo')")
                1 / 0
        with pool.connection() as c:
            c.execute("insert into marca (texto) values ('terceiro')")
    finally:
        cx.fechar_requisicao(token)

    assert _marcas(pool) == ["primeiro", "terceiro"], \
        "o bloco que falhou desfaz só o dele; os outros ficam"


def test_a_conexao_volta_pro_pool_no_fim(pool):
    """Segurar a conexão além da requisição esvaziaria o pool em minutos."""
    token = cx.abrir_requisicao()
    with pool.connection() as c:
        c.execute("select 1")
    cx.fechar_requisicao(token)

    st = pool.get_stats()
    assert st.get("pool_available", 0) >= 1, f"conexão não voltou: {st}"


def test_fora_de_requisicao_nada_muda(pool):
    """Poller, crons, scripts e a própria suíte: cada bloco continua sendo a sua
    conexão, como sempre foi."""
    med = medicao.abrir()
    try:
        vistos = []
        for _ in range(2):
            with pool.connection() as c:
                vistos.append(id(c))
                c.execute("select 1")
    finally:
        m = medicao.fechar(med)
    assert m["conexoes"] == 2


def test_a_porta_de_saida_existe(pool, monkeypatch):
    """`PG_CONEXAO_POR_REQUISICAO=0` volta ao comportamento anterior sem deploy —
    se algo estranho aparecer em produção, a saída é uma variável de ambiente."""
    monkeypatch.setenv("PG_CONEXAO_POR_REQUISICAO", "0")
    token = cx.abrir_requisicao()
    assert token is None
    med = medicao.abrir()
    try:
        for _ in range(2):
            with pool.connection() as c:
                c.execute("select 1")
    finally:
        m = medicao.fechar(med)
        cx.fechar_requisicao(token)
    assert m["conexoes"] == 2


def test_fechar_sem_ter_aberto_nao_explode(pool):
    """Chamado no `finally` do middleware: ele roda mesmo quando a rota morreu
    antes de tocar no banco."""
    cx.fechar_requisicao(None)
    token = cx.abrir_requisicao()
    cx.fechar_requisicao(token)      # nunca pediu conexão nenhuma
