"""PASSO A do plano de RLS: o app passa a ANUNCIAR de quem é a requisição.

Escreve `app.conta_id` na conexão do Postgres. Hoje isso é INERTE: não existe
nenhuma policy lendo essa variável, e o papel `postgres` fura a RLS de qualquer
jeito (BYPASSRLS + dono de todas as tabelas). É de propósito — a ideia é ter o
lado do app pronto e testado ANTES de existir qualquer policy, pra que o Passo B
(as policies) seja só um `create policy`, sem mexer em código no mesmo dia.

DESLIGADO POR PADRÃO. Só age com RLS_SET_CONTA=1 no ambiente. Com a flag off o
custo é uma leitura de variável de ambiente por conexão pega do pool — nada mais.

⚠ POOLER: se a DATABASE_URL apontar pro pooler do Supabase em modo TRANSAÇÃO
(porta 6543), a conexão de servidor pode trocar entre transações e um SET de
SESSÃO não gruda. Por isso existe o scripts/diag_tenant.py: ele mede isso contra
o banco de verdade em vez de a gente supor. Na porta 5432 (direta/sessão) gruda.
"""
from __future__ import annotations

import logging
import os
from contextvars import ContextVar

_log = logging.getLogger("openclaw.tenant")

# De quem é a requisição em curso. ContextVar (e não variável global) porque as
# rotas são `def` e o Starlette as roda em threadpool: cada requisição precisa
# do seu próprio valor, sem vazar pra requisição vizinha.
CONTA_ATUAL: ContextVar[int | None] = ContextVar("conta_atual", default=None)


def ligado() -> bool:
    """A flag é lida a cada uso de propósito: dá pra ligar/desligar sem deploy."""
    return os.environ.get("RLS_SET_CONTA") == "1"


def aplicar(conn, conta_id: int | None = None) -> None:
    """Escreve app.conta_id na conexão. BEST-EFFORT: nunca derruba a query.

    Se falhar, só loga. Enquanto não existir policy, não anunciar a conta não
    muda nada; derrubar uma requisição por causa disso seria muito pior.
    """
    if not ligado():
        return
    cid = conta_id if conta_id is not None else CONTA_ATUAL.get()
    try:
        # set_config(..., false) = escopo de SESSÃO (vale até a conexão voltar
        # a ser reconfigurada). Sempre escrevemos, inclusive string vazia quando
        # não há conta: assim uma conexão reaproveitada nunca herda o tenant de
        # quem a usou antes — que seria exatamente o vazamento a evitar.
        conn.execute("select set_config('app.conta_id', %s, false)",
                     (str(cid) if cid else "",))
    except Exception as e:  # noqa: BLE001
        _log.warning("não deu pra anunciar app.conta_id (%s): %s",
                     cid, type(e).__name__)
