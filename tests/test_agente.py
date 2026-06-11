"""Testa o loop da Fabrica de agentes sem precisar da API.

Um "cerebro falso" finge as respostas do Claude: primeiro pede pra usar a
ferramenta lancar_despesa, depois responde em texto. Validamos que a ferramenta
de fato gravou no livro-caixa.
"""
import os
from types import SimpleNamespace

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from usuarios import usuarios as u
from core.agent import criar_agente
from core.memory import MemoriaConversa
from finance.livro_caixa import LivroCaixa
from finance.tools import construir_ferramentas


@pytest.fixture()
def pool():
    p = ConnectionPool(os.environ["DATABASE_URL"], min_size=1, max_size=4, open=True)
    init_schema(p)
    with p.connection() as c:
        c.execute("truncate usuarios, lancamentos, uso_diario restart identity cascade")
        c.commit()
    yield p
    p.close()


class CerebroFalso:
    """1a chamada: pede tool_use lancar_despesa. 2a: responde em texto."""
    def __init__(self):
        self.chamadas = 0

    def chamar(self, system, mensagens, ferramentas=None):
        self.chamadas += 1
        if self.chamadas == 1:
            bloco = SimpleNamespace(
                type="tool_use", id="t1", name="lancar_despesa",
                input={"valor": 87.40, "categoria": "Mercado", "descricao": "feira"},
            )
            return SimpleNamespace(stop_reason="tool_use", content=[bloco])
        texto = SimpleNamespace(type="text", text="Pronto, registrei R$ 87,40 em Mercado.")
        return SimpleNamespace(stop_reason="end_turn", content=[texto])


def test_loop_grava_no_livro(pool):
    ana = u.get_or_create(pool, telegram_id=42, nome="Ana")
    livro = LivroCaixa(pool, ana.id)
    agente = criar_agente(
        nome="Financeiro", persona="teste",
        ferramentas=construir_ferramentas(livro),
        brain=CerebroFalso(), memoria=MemoriaConversa(),
    )

    resposta = agente.responder("gastei 87,40 na feira")

    assert "registrei" in resposta.lower()
    lancs = livro.listar()
    assert len(lancs) == 1
    assert lancs[0].valor_centavos == 8740
    assert lancs[0].categoria == "Mercado"
    assert livro.saldo_centavos() == -8740
