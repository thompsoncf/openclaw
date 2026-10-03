"""A trava de um ciclo do poller que vale atrás do pooler do Supabase (migração 610).

POR QUE NÃO `pg_try_advisory_lock`. A trava de sessão do Postgres pertence à conexão de
SERVIDOR. O pooler do Supabase entrega cada comando a uma conexão de servidor qualquer:
a trava é pega numa, o `pg_advisory_unlock` cai em outra e falha ("you don't own a lock"),
a trava fica presa na primeira, e todo worker que cair nela "pega" de novo. Foi assim que,
em 03/10/2026, dois ciclos do resgate rodaram juntos e um cliente da Prime recebeu a
mensagem que a IA tinha decidido não mandar (ver a 610).

AQUI A TRAVA É UMA LINHA da tabela `travas`: o INSERT … ON CONFLICT é atômico em
qualquer conexão, só leva quem gravou, e solta apagando a própria linha. O prazo
(`validade`) cobre o processo que morre no meio: vencido, o próximo ciclo pega.

Banco sem a 610 → a trava antiga (advisory), pra suíte de schema mínimo e o deploy em
que o código chega antes da migração não pararem o ciclo.
"""
from __future__ import annotations

import logging
import os
import socket
import uuid
from contextlib import contextmanager
from datetime import timedelta

_log = logging.getLogger("openclaw.trava")

#: o ciclo mais longo do resgate (a IA escrevendo + um envio) leva segundos; 15 minutos
#: é folga de sobra e, se o processo morrer, o resgate volta sozinho em 15 minutos
VALIDADE = timedelta(minutes=15)


def _dono() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def pegar(pool, nome: str, dono: str, validade: timedelta = VALIDADE) -> bool | None:
    """Tenta levar a trava. True: é sua. False: outro está com ela. None: banco sem a 610."""
    try:
        with pool.connection() as c:
            r = c.execute(
                """insert into travas (nome, dono, expira_em) values (%s, %s, now() + %s)
                   on conflict (nome) do update
                      set dono = excluded.dono, expira_em = excluded.expira_em, pegou_em = now()
                    where travas.expira_em < now()
                   returning dono""", (nome, dono, validade)).fetchone()
            c.commit()
    except Exception as e:  # noqa: BLE001
        if "travas" in str(e) and "does not exist" in str(e):
            return None
        raise
    return bool(r and r[0] == dono)


def soltar(pool, nome: str, dono: str) -> None:
    """Solta a trava — só se ainda é de quem pegou (se o prazo venceu e outro levou, não
    é desta passada apagar a dele)."""
    try:
        with pool.connection() as c:
            c.execute("delete from travas where nome=%s and dono=%s", (nome, dono))
            c.commit()
    except Exception as e:  # noqa: BLE001 — o prazo solta sozinho
        _log.warning("trava.soltar(%s): %s: %s", nome, type(e).__name__, e)


@contextmanager
def ciclo(pool, nome: str, lock_antigo: int, validade: timedelta = VALIDADE):
    """`with ciclo(pool, "resgate", _LOCK) as pegou: if not pegou: return`.

    `lock_antigo` é o número da trava advisory de antes, usado só no banco sem a 610."""
    dono = _dono()
    try:
        pegou = pegar(pool, nome, dono, validade)
    except Exception as e:  # noqa: BLE001 — sem banco: este ciclo não roda
        _log.warning("trava.ciclo(%s): %s: %s", nome, type(e).__name__, e)
        yield False
        return
    if pegou is None:
        with _advisory(pool, lock_antigo) as p:
            yield p
        return
    try:
        yield pegou
    finally:
        if pegou:
            soltar(pool, nome, dono)


@contextmanager
def _advisory(pool, lock: int):
    """A trava de antes (banco sem a 610). Fora de transação: o ciclo pode passar do
    `idle_in_transaction_session_timeout` enquanto a IA escreve."""
    with pool.connection() as lk:
        lk.commit()
        lk.autocommit = True
        try:
            try:
                pegou = bool(lk.execute("select pg_try_advisory_lock(%s)", (lock,)).fetchone()[0])
            except Exception:  # noqa: BLE001
                pegou = False
            try:
                yield pegou
            finally:
                if pegou:
                    lk.execute("select pg_advisory_unlock(%s)", (lock,))
        finally:
            lk.autocommit = False
