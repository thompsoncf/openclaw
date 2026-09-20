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


def abrir(detalhe: bool = False):
    """Começa a medir a requisição corrente. Devolve o token pro `fechar`.

    `detalhe=True` guarda também QUAIS consultas foram — o "46 consultas" do
    cabeçalho diz que a tela está cara, mas não diz o que cortar. Fica desligado
    em produção: guardar uma lista por requisição é barato, mas o que ela serve é
    pra quem está otimizando, e isso se faz na suíte (ver
    `test_a_fila_nao_pode_voltar_a_conversar_46_vezes_com_o_banco`)."""
    m = {"conexoes": 0, "consultas": 0, "ms": 0.0}
    if detalhe:
        m["sqls"] = []
    return MEDIDA.set(m)


def fechar(token) -> dict:
    """Para de medir e devolve o que foi somado."""
    m = MEDIDA.get() or {"conexoes": 0, "consultas": 0, "ms": 0.0}
    MEDIDA.reset(token)
    return m


def resumir_sql(sql) -> str:
    """A consulta em uma linha, sem os valores: `select p.id, p.empresa from
    prospeccao p where ...` vira `select … from prospeccao`.

    É o suficiente pra reconhecer a repetida — e é justamente a repetida que se
    corta."""
    texto = " ".join(str(sql).split())[:400].lower()
    verbo = texto.split(" ", 1)[0] if texto else "?"
    alvo = ""
    for marca in (" from ", " into ", " update "):
        if marca in texto:
            alvo = texto.split(marca, 1)[1].split(" ")[0].strip("(,")
            break
    return f"{verbo} … {alvo}".strip() if alvo else texto[:60]


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
            gasto = (time.perf_counter() - t0) * 1000
            m["consultas"] += 1
            m["ms"] += gasto
            if "sqls" in m:
                m["sqls"].append((resumir_sql(args[0] if args else ""), round(gasto, 1)))
