"""O TETO POR CHIP das mensagens AUTOMÁTICAS ao cliente (revisão de 27/09/2026).

POR QUE EXISTE. Cada motor tinha o seu freio (o resgate, 20 por dia com 20 min entre
dois; a IA insiste, 20 por dia; as rotinas da visita, 6 por ciclo) e nenhum olhava os
outros. Num dia cheio, às 18h, as vésperas das visitas, os lembretes do sinal, o
pós-festa e os toques saíam JUNTOS pelo mesmo número — e chip de QR que dispara em
rajada é chip banido. Aqui é uma conta só, por chip: o que as rotinas mandaram
(`envios_automaticos`) mais o que o resgate e a IA insiste mandaram (`resgate_envios`).

O QUE CONTA: só mensagem que o SISTEMA inicia (confirmação, lembrete, toque,
agradecimento). A resposta da IA a quem escreveu não conta — ela é conversa, não
disparo.

Tolerante: sem a tabela (antes da migração 425) ou com erro na conta, deixa passar —
o freio de cada motor continua valendo, como antes.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from finance import agenda as ag

_log = logging.getLogger(__name__)

#: por chip. Folgados pro volume de hoje (a Prime faz ~1 visita por dia) e baixos o
#: bastante pra que nenhum dia vire rajada.
POR_HORA = 20
POR_DIA = 80

#: os tipos de `resgate_envios` que foram ao CLIENTE
_TIPOS_RESGATE = ("retomada", "toque_ia", "toque_2", "toque_3", "toque", "repescagem")


def _chip(c, conta_id: int, conversa_id: int | None):
    if not conversa_id:
        return None
    r = c.execute("select chip_id from conversas where id=%s and conta_id=%s",
                  (conversa_id, conta_id)).fetchone()
    return r[0] if r else None


def contagem(c, conta_id: int, conversa_id: int | None, agora: datetime) -> tuple[int, int]:
    """(na última hora, hoje) — no chip desta conversa."""
    chip = _chip(c, conta_id, conversa_id)
    loc = agora.astimezone(ag.BRT)
    inicio_dia = datetime(loc.year, loc.month, loc.day, tzinfo=ag.BRT)
    hora = agora - timedelta(hours=1)
    r = c.execute(
        """select count(*) filter (where criado_em > %(h)s), count(*)
             from envios_automaticos
            where conta_id=%(c)s and chip_id is not distinct from %(chip)s
              and criado_em >= %(d)s""",
        {"c": conta_id, "chip": chip, "h": hora, "d": inicio_dia}).fetchone()
    n_hora, n_dia = int(r[0] or 0), int(r[1] or 0)
    try:
        with c.transaction():
            r2 = c.execute(
                """select count(*) filter (where re.criado_em > %(h)s), count(*)
                     from resgate_envios re
                    where re.conta_id=%(c)s and re.ok and re.criado_em >= %(d)s
                      and re.tipo = any(%(t)s)
                      and (select cv.chip_id from conversas cv
                            where cv.conta_id = re.conta_id and cv.prospeccao_id = re.prospeccao_id
                              and cv.canal = 'whatsapp' order by cv.id desc limit 1)
                          is not distinct from %(chip)s""",
                {"c": conta_id, "chip": chip, "h": hora, "d": inicio_dia,
                 "t": list(_TIPOS_RESGATE)}).fetchone()
        n_hora += int(r2[0] or 0)
        n_dia += int(r2[1] or 0)
    except Exception:  # noqa: BLE001 — base sem o resgate: só as rotinas contam
        pass
    return n_hora, n_dia


def pode(c, conta_id: int, conversa_id: int | None, agora: datetime) -> bool:
    """Cabe mais uma mensagem automática neste chip agora?"""
    try:
        with c.transaction():
            n_hora, n_dia = contagem(c, conta_id, conversa_id, agora)
    except Exception:  # noqa: BLE001 — sem a 425: o freio de cada motor vale
        return True
    if n_hora >= POR_HORA or n_dia >= POR_DIA:
        _log.info("teto_chip: conta %s, conversa %s: %s na hora, %s hoje — segurou",
                  conta_id, conversa_id, n_hora, n_dia)
        return False
    return True


def registrar(c, conta_id: int, conversa_id: int | None, origem: str,
              agora: datetime | None = None) -> None:
    """Anota uma mensagem automática que SAIU. Best-effort: nunca derruba o envio."""
    try:
        with c.transaction():
            c.execute("""insert into envios_automaticos (conta_id, chip_id, conversa_id, origem,
                                                         criado_em)
                         values (%s,%s,%s,%s, coalesce(%s, now()))""",
                      (conta_id, _chip(c, conta_id, conversa_id), conversa_id, origem, agora))
    except Exception:  # noqa: BLE001
        pass
