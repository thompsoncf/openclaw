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

DOIS JEITOS. `ciclo`: o passo do poller que só um worker roda (o outro passa a vez).
`esperar`: a exclusão de um passo curto (marcar visita, reservar data, vender estoque),
que espera a vez com prazo.

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
#: a trava de exclusão (`esperar`) cobre um passo de segundos (marcar, reservar, vender)
VALIDADE_CURTA = timedelta(minutes=2)


def _dono() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def pegar(pool, nome: str, dono: str, validade: timedelta = VALIDADE) -> bool | None:
    """Tenta levar a trava. True: é sua. False: outro está com ela. None: banco sem a 610.

    Pergunta se a tabela existe ANTES (`to_regclass`), em vez de deixar o INSERT falhar:
    um erro abortaria a transação de quem divide a conexão (a requisição, a suíte)."""
    with pool.connection() as c:
        if not c.execute("select to_regclass('public.travas') is not null").fetchone()[0]:
            return None
        r = c.execute(
            """insert into travas (nome, dono, expira_em) values (%s, %s, now() + %s)
               on conflict (nome) do update
                  set dono = excluded.dono, expira_em = excluded.expira_em, pegou_em = now()
                where travas.expira_em < now()
               returning dono""", (nome, dono, validade)).fetchone()
        c.commit()
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
def esperar(pool, nome: str, lock_antigo, *, prazo_s: float = 15,
            validade: timedelta = VALIDADE_CURTA):
    """A trava de EXCLUSÃO que espera a vez, com prazo: duas aprovações no mesmo segundo
    não seguram a mesma data, duas marcações não pegam o mesmo horário, duas vendas não
    levam o mesmo estoque. `with esperar(pool, f"visita:{conta}", (_LOCK, conta)) as
    pegou: if not pegou: <ocupado>`. Esperar é tentar de novo a cada 0,2 s: nenhuma
    conexão fica presa no pool esperando.

    `validade` curta: é trava de um passo de segundos — se o processo morrer segurando,
    a próxima tentativa passa em 2 minutos, não em 15."""
    import time
    dono = _dono()
    fim = time.monotonic() + prazo_s
    while True:
        try:
            pegou = pegar(pool, nome, dono, validade)
        except Exception as e:  # noqa: BLE001 — sem banco: não pega
            _log.warning("trava.esperar(%s): %s: %s", nome, type(e).__name__, e)
            pegou = False
        if pegou is None:
            with _advisory(pool, lock_antigo, prazo_s=max(0.0, fim - time.monotonic())) as p:
                yield p
            return
        if pegou or time.monotonic() > fim:
            break
        time.sleep(0.2)
    try:
        yield pegou
    finally:
        if pegou:
            soltar(pool, nome, dono)


@contextmanager
def _advisory(pool, lock, prazo_s: float = 0):
    """A trava de antes (banco sem a 610): `lock` é um número ou um par (a, b). Fora de
    transação: o ciclo pode passar do `idle_in_transaction_session_timeout` enquanto a
    IA escreve. Com `prazo_s`, tenta de novo até o prazo."""
    import time
    par = tuple(lock) if isinstance(lock, (tuple, list)) else (lock,)
    sql = ("select pg_try_advisory_lock(%s::int, %s::int)" if len(par) == 2
           else "select pg_try_advisory_lock(%s)")
    fim = time.monotonic() + prazo_s
    with pool.connection() as lk:
        lk.commit()
        lk.autocommit = True
        try:
            pegou = False
            while True:
                try:
                    pegou = bool(lk.execute(sql, par).fetchone()[0])
                except Exception:  # noqa: BLE001
                    pegou = False
                if pegou or time.monotonic() > fim:
                    break
                time.sleep(0.2)
            try:
                yield pegou
            finally:
                if pegou:
                    lk.execute(sql.replace("pg_try_advisory_lock", "pg_advisory_unlock"), par)
        finally:
            lk.autocommit = False
