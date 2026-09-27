"""OS AVISOS À EQUIPE ESPERAM O DIA (revisão de 27/09/2026).

POR QUE EXISTE. A reserva que vencia às 3h mandava, na hora, o Telegram do dono; e
os avisos da IA pra equipe (`chip_regra.notificar`: "visita marcada pela IA",
"orçamento pra conferir", "comprovante chegou", "data liberada"…) saíam por push,
e-mail e WhatsApp a qualquer hora. Nada disso é de madrugada: ninguém age antes das
8h, e o celular tocando às 3h é o jeito certo de fazer a equipe silenciar o app.

COMO. Fora da janela (8h às 21h, Brasília — a mesma de `visita_rotinas.HORAS_EQUIPE`),
o aviso vira uma linha em `avisos_adiados`. O relógio (`lembretes`) solta as linhas
no primeiro ciclo da manhã, cada uma uma vez só. A REGRA DO NEGÓCIO NÃO ESPERA: a
data vencida é liberada na hora, o card anda na hora — só o aviso espera.

Tolerante: sem a tabela (antes da migração 432), o aviso sai na hora, como antes.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from finance.agenda import BRT

_log = logging.getLogger(__name__)

HORAS = (8, 21)
#: o que a manhã solta por ciclo — sobra, fica pro ciclo seguinte (~2 min)
POR_CICLO = 30


def dentro(agora: datetime | None = None) -> bool:
    agora = agora or datetime.now(timezone.utc)
    return HORAS[0] <= agora.astimezone(BRT).hour < HORAS[1]


def adiar(pool, conta_id: int, *, membro_id: int | None, titulo: str, corpo: str,
          url: str = "", destino: str = "membro") -> bool:
    """Guarda o aviso pra manhã. `destino`: 'membro' (push, e-mail e WhatsApp da
    pessoa, como `chip_regra.notificar`), 'festa' (WhatsApp dos avisos e push, como
    `visita_rotinas.avisar`) ou 'dono' (o Telegram do dono, com `corpo` como texto).
    False se não deu pra guardar — aí quem chamou manda na hora."""
    try:
        with pool.connection() as c:
            c.execute("""insert into avisos_adiados (conta_id, membro_id, destino, titulo, corpo, url)
                         values (%s,%s,%s,%s,%s,%s)""",
                      (conta_id, membro_id, destino, titulo or "", corpo or "", url or ""))
            c.commit()
        return True
    except Exception as e:  # noqa: BLE001 — sem a 432: sai na hora
        _log.info("aviso_noite.adiar: não guardou (conta %s): %s", conta_id, e)
        return False


def soltar(pool, agora: datetime | None = None) -> int:
    """Manda os avisos guardados, se já é dia. Cada linha é reivindicada (marca
    `enviado_em`) ANTES de sair: dois workers nunca mandam o mesmo aviso."""
    if not dentro(agora):
        return 0
    try:
        with pool.connection() as c:
            rows = c.execute(
                """update avisos_adiados set enviado_em = now()
                    where id in (select id from avisos_adiados where enviado_em is null
                                  order by id limit %s for update skip locked)
                returning id, conta_id, membro_id, destino, titulo, corpo, url""",
                (POR_CICLO,)).fetchall()
            c.commit()
    except Exception as e:  # noqa: BLE001
        _log.info("aviso_noite.soltar: %s", e)
        return 0
    n = 0
    for _id, conta, membro, destino, titulo, corpo, url in rows:
        try:
            if destino == "dono":
                from finance import notificar as _nt
                _nt.enviar_para_dono(pool, conta, corpo)
            elif destino == "festa" and membro:
                from finance import visita_rotinas as _vr
                _vr.avisar(pool, conta, membro, titulo, corpo, url, origem="festa")
            elif membro:
                from finance import chip_regra as _cr
                _cr.notificar(pool, conta, membro, titulo, corpo, url, adiar_de_noite=False)
            n += 1
        except Exception as e:  # noqa: BLE001 — um aviso que falha não segura os outros
            _log.info("aviso_noite.soltar: aviso %s não saiu: %s", _id, e)
    return n
