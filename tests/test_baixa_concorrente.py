"""Regressão da baixa de título (finance/empresa.dar_baixa_titulo).

Blinda o caixa contra o DOBRO de lançamento: antes, a baixa lia status='aberto'
e SÓ DEPOIS lançava no livro-caixa e virava o status. Duas chamadas concorrentes
(webhook Asaas duplicado ou duplo-toque na tela) passavam as duas pela leitura e
AMBAS lançavam — dobrando a receita/despesa. A correção torna a baixa atômica:
UPDATE ... WHERE status='aberto' RETURNING antes de lançar; só quem ganha a
corrida lança.

Roda com um banco de TESTE separado (nunca produção) — ver tests/conftest.py:
    export TEST_DATABASE_URL="postgresql://.../banco_de_teste"
    pytest
"""
import os
import threading
from datetime import date
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from finance import empresa

# Migrações que criam o que a baixa toca: chave (018), titulos (053),
# natureza em lancamentos (057). schema.sql cria contas/membros/lancamentos.
_MIGRACOES = ("018_chave_nfce_lancamentos.sql",
              "053_modulo_pj.sql",
              "057_natureza_lancamento.sql")


@pytest.fixture(scope="module")
def pool():
    # TEST_DATABASE_URL é garantida pela trava do conftest (pytest_configure).
    test_db_url = os.environ["TEST_DATABASE_URL"]
    p = ConnectionPool(test_db_url, min_size=1, max_size=6, open=True,
                       kwargs={"prepare_threshold": None})
    init_schema(p)  # idempotente (create table if not exists / on conflict)
    base = Path(__file__).resolve().parent.parent / "db" / "migracoes"
    for m in _MIGRACOES:
        with p.connection() as c:
            c.execute((base / m).read_text(encoding="utf-8"))
            c.commit()
    yield p
    p.close()


@pytest.fixture()
def conta_id(pool):
    """Uma conta nova por teste — isola cada caso sem truncate global."""
    with pool.connection() as c:
        cid = c.execute(
            "insert into contas (tipo, nome) values ('pj', 'Teste Baixa') returning id"
        ).fetchone()[0]
        c.commit()
    return cid


def _cria_titulo(pool, conta_id, *, tipo="receber", valor=5000,
                 recorrente=False, venc=None):
    venc = venc or date.today()
    with pool.connection() as c:
        tid = c.execute(
            """insert into titulos
                 (conta_id, tipo, descricao, valor_centavos, vencimento, recorrente)
               values (%s, %s, 'Título de teste', %s, %s, %s) returning id""",
            (conta_id, tipo, valor, venc, recorrente),
        ).fetchone()[0]
        c.commit()
    return tid


def _conta_lancamentos(pool, conta_id):
    with pool.connection() as c:
        return c.execute(
            "select count(*) from lancamentos where conta_id=%s", (conta_id,)
        ).fetchone()[0]


def test_baixa_concorrente_nao_duplica_lancamento(pool, conta_id):
    """Duas baixas simultâneas do MESMO título → 1 lançamento, 1 sucesso.

    Sem a baixa atômica, as duas threads liam 'aberto' e lançavam as duas."""
    tid = _cria_titulo(pool, conta_id, valor=5000)

    barreira = threading.Barrier(2)
    resultados = []

    def worker():
        barreira.wait()  # dispara as duas ao mesmo tempo → maximiza a corrida
        resultados.append(empresa.dar_baixa_titulo(pool, conta_id, tid))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    sucessos = [r for r in resultados if r.get("ok")]
    assert len(sucessos) == 1, f"esperava 1 baixa vencedora, veio {resultados}"
    assert _conta_lancamentos(pool, conta_id) == 1, "lançamento duplicado no caixa!"


def test_baixa_sequencial_e_idempotente(pool, conta_id):
    """Bater a baixa duas vezes seguidas: a 2ª não lança de novo."""
    tid = _cria_titulo(pool, conta_id, valor=7000)

    r1 = empresa.dar_baixa_titulo(pool, conta_id, tid)
    assert r1["ok"] is True

    r2 = empresa.dar_baixa_titulo(pool, conta_id, tid)
    assert r2["ok"] is False
    assert "pago" in r2["erro"].lower()

    assert _conta_lancamentos(pool, conta_id) == 1


def test_baixa_recorrente_cria_proximo_uma_vez(pool, conta_id):
    """Título recorrente: a baixa gera o do mês seguinte e lança UMA vez."""
    hoje = date.today()
    tid = _cria_titulo(pool, conta_id, tipo="pagar", valor=30000,
                       recorrente=True, venc=hoje)

    r = empresa.dar_baixa_titulo(pool, conta_id, tid)
    assert r["ok"] is True
    assert r["proximo_titulo_id"] is not None

    with pool.connection() as c:
        prox = c.execute(
            "select status, recorrente, vencimento from titulos where id=%s",
            (r["proximo_titulo_id"],),
        ).fetchone()
    assert prox[0] == "aberto"
    assert prox[1] is True
    assert prox[2] > hoje, "o próximo título deveria vencer depois do atual"

    # baixa gerou exatamente 1 lançamento (o do título atual)
    assert _conta_lancamentos(pool, conta_id) == 1
