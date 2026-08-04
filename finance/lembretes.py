"""Lembretes proativos da agenda (etapa 2): "resumo do dia" + "aviso antes".

Roda no ticker de fundo do web (a cada ~2 min). Desenho da idempotência:
- advisory lock (pg_try_advisory_lock): só um worker processa por tick;
- tabela lembretes_enviados: cada resumo (1 por conta por dia) e cada aviso
  (1 por evento) sai UMA vez — o ticker pode rodar dezenas de vezes sem repetir.

O aviso dispara quando o evento entra na janela (começa daqui a <= aviso_antes_min);
como é deduplicado por evento, sai só na primeira vez que entra na janela.

Tudo em horário de Brasília (a agenda guarda timestamptz aware).
"""
from __future__ import annotations

import logging
from datetime import timedelta

from . import agenda as ag
from . import convites as cv
from . import notificar

_log = logging.getLogger("openclaw.lembretes")
_LOCK = 553311   # advisory lock (só um worker dispara por vez)


def rodar(pool, agora=None) -> dict:
    """Ponto de entrada do ticker. Nunca levanta — só registra e segue."""
    try:
        return _rodar(pool, agora or ag.agora_brt())
    except Exception as e:  # noqa: BLE001
        _log.info("lembretes.rodar falhou: %s: %s", type(e).__name__, e)
        return {"resumo": 0, "aviso": 0}


def _rodar(pool, agora) -> dict:
    n_res = n_avi = 0
    with pool.connection() as lockc:
        if not lockc.execute("select pg_try_advisory_lock(%s)", (_LOCK,)).fetchone()[0]:
            return {"resumo": 0, "aviso": 0}
        try:
            with pool.connection() as c:
                cfgs = c.execute(
                    "select conta_id, resumo_ativo, hora_resumo, aviso_antes_min "
                    "from agenda_config "
                    "where resumo_ativo or aviso_antes_min is not null").fetchall()
            for (conta_id, resumo_ativo, hora_resumo, aviso_antes_min) in cfgs:
                if resumo_ativo and hora_resumo is not None and agora.hour == hora_resumo:
                    n_res += _resumo_do_dia(pool, conta_id, agora)
                if aviso_antes_min:
                    n_avi += _avisos_proximos(pool, conta_id, int(aviso_antes_min), agora)
        finally:
            lockc.execute("select pg_advisory_unlock(%s)", (_LOCK,))
            lockc.commit()
    return {"resumo": n_res, "aviso": n_avi}


def _primeira_vez(pool, conta_id: int, tipo: str, chave: str) -> bool:
    """Registra o envio; True se é a 1ª vez (deve enviar), False se já saiu."""
    with pool.connection() as c:
        cur = c.execute(
            "insert into lembretes_enviados (conta_id, tipo, chave) values (%s,%s,%s) "
            "on conflict (conta_id, tipo, chave) do nothing", (conta_id, tipo, chave))
        c.commit()
        return cur.rowcount > 0


def _resumo_do_dia(pool, conta_id: int, agora) -> int:
    """Manda a agenda de HOJE (só se tiver algo e ainda não mandou hoje)."""
    inicio = agora.replace(hour=0, minute=0, second=0, microsecond=0)
    eventos = ag.listar_eventos(pool, conta_id, inicio, inicio + timedelta(days=1))
    if not eventos:
        return 0
    if not _primeira_vez(pool, conta_id, "resumo", agora.strftime("%Y-%m-%d")):
        return 0
    linhas = ["☀️ *Bom dia!* Sua agenda de hoje:"]
    for ev in eventos:
        h = ev["inicio"].astimezone(ag.BRT).strftime("%H:%M")
        loc = f" — {ev['local']}" if ev.get("local") else ""
        linhas.append(f"• *{h}* {ev['titulo']}{loc}")
    return 1 if notificar.enviar_para_dono(pool, conta_id, "\n".join(linhas)) else 0


def _avisos_proximos(pool, conta_id: int, antes_min: int, agora) -> int:
    """Avisa dos eventos que começam de agora até daqui a `antes_min` minutos —
    pro dono (Telegram, sempre) e pros convidados CONFIRMADOS (WhatsApp, só se a
    conta tiver o template de lembrete aprovado — ver convites.template_lembrete_
    configurado; enquanto não tiver, essa parte só não faz nada)."""
    eventos = ag.listar_eventos(pool, conta_id, agora, agora + timedelta(minutes=antes_min))
    n = 0
    tem_template = cv.template_lembrete_configurado()
    for ev in eventos:
        h = ev["inicio"].astimezone(ag.BRT).strftime("%H:%M")
        faltam = max(0, int((ev["inicio"] - agora).total_seconds() // 60))
        if _primeira_vez(pool, conta_id, "aviso", f"evt:{ev['id']}"):
            loc = f"\n📍 {ev['local']}" if ev.get("local") else ""
            txt = f"⏰ *Daqui a pouco* (em ~{faltam} min): *{ev['titulo']}* às {h}.{loc}"
            if notificar.enviar_para_dono(pool, conta_id, txt):
                n += 1
        if tem_template:
            n += _avisar_convidados_confirmados(pool, conta_id, ev, h, faltam)
    return n


def _avisar_convidados_confirmados(pool, conta_id: int, ev: dict, hora: str,
                                   faltam: int) -> int:
    """Manda o mesmo 'tá chegando a hora' pra quem CONFIRMOU presença nesse evento
    — dedup por convidado (não pelo evento, já consumido pelo aviso do dono)."""
    n = 0
    convidados = cv.por_evento(pool, conta_id, [ev["id"]]).get(ev["id"], [])
    for g in convidados:
        if g["status"] != "confirmado" or not (g.get("contato") or "").strip():
            continue
        if not _primeira_vez(pool, conta_id, "aviso_convidado", f"evt:{ev['id']}:conv:{g['id']}"):
            continue
        r = cv.enviar_lembrete_whatsapp(pool, conta_id, g["contato"], ev["titulo"], hora, faltam)
        if r.get("ok"):
            n += 1
    return n
