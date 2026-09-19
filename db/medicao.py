"""Quanto de banco cada requisição gasta: conexões, consultas e tempo.

Existe pra responder com NÚMERO a pergunta "por que o app está lento". Medido em
19/09/2026: a fila do vendedor faz ~12 consultas por carga e a conversa aberta
refazia ~10 a cada 8 s — mas isso foi lido no código, não na requisição de
verdade. Com esta medição o log diz, por tela, quantas foram e quanto custaram,
e o antes/depois de cada otimização deixa de ser impressão.

COMO FUNCIONA. O middleware abre uma MEDIDA (um dict) no começo da requisição e
lê no fim. O pool conta cada conexão entregue e a conexão conta cada `execute`.

Por que um dict MUTÁVEL dentro do ContextVar, e não contadores no ContextVar: as
rotas são `def`, e o Starlette as roda numa thread com uma CÓPIA do contexto. Um
`.set()` feito lá dentro morreria na cópia; mexer no mesmo dict, não — o objeto é
o mesmo dos dois lados.

Fora de uma requisição medida (poller, scripts, testes) a MEDIDA é None e contar
custa um `if`. Nada aqui pode derrubar uma consulta.
"""
from __future__ import annotations

import time
from contextvars import ContextVar

import psycopg

MEDIDA: ContextVar[dict | None] = ContextVar("medida_banco", default=None)


def abrir():
    """Começa a medir a requisição corrente. Devolve o token pro `fechar`."""
    return MEDIDA.set({"conexoes": 0, "consultas": 0, "ms": 0.0})


def fechar(token) -> dict:
    """Para de medir e devolve o que foi somado."""
    m = MEDIDA.get() or {"conexoes": 0, "consultas": 0, "ms": 0.0}
    MEDIDA.reset(token)
    return m


def contar_conexao() -> None:
    m = MEDIDA.get()
    if m is not None:
        m["conexoes"] += 1


class ConexaoMedida(psycopg.Connection):
    """Uma `psycopg.Connection` que soma as próprias consultas na MEDIDA.

    Só o `execute` da conexão é contado — é o jeito que o app usa em quase todo
    lugar (`c.execute(...)`). O tempo inclui a ida e volta até o banco, que é
    justamente o custo que interessa quando o servidor e o banco não moram juntos.
    """

    def execute(self, *args, **kwargs):
        m = MEDIDA.get()
        if m is None:
            return super().execute(*args, **kwargs)
        t0 = time.perf_counter()
        try:
            return super().execute(*args, **kwargs)
        finally:
            m["consultas"] += 1
            m["ms"] += (time.perf_counter() - t0) * 1000
