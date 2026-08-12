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
                    "select conta_id, resumo_ativo, hora_resumo, aviso_antes_min, avisar_convidados "
                    "from agenda_config "
                    "where resumo_ativo or aviso_antes_min is not null").fetchall()
            for (conta_id, resumo_ativo, hora_resumo, aviso_antes_min, avisar_convidados) in cfgs:
                if resumo_ativo and hora_resumo is not None and agora.hour == hora_resumo:
                    n_res += _resumo_do_dia(pool, conta_id, agora)
                if aviso_antes_min:
                    n_avi += _avisos_proximos(pool, conta_id, int(aviso_antes_min), agora,
                                              bool(avisar_convidados))
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


def _avisos_proximos(pool, conta_id: int, antes_min: int, agora,
                     avisar_convidados: bool = True) -> int:
    """Avisa dos eventos que começam de agora até daqui a `antes_min` minutos —
    pro dono (Telegram, sempre) e, se `avisar_convidados` estiver ligado (switch
    "Avisar os convidados" no painel), pros convidados CONFIRMADOS (WhatsApp —
    veja convites.avisar_convidado_confirmado: livre dentro da janela de 24h
    desde a confirmação dele, template aprovado fora dela; sem nenhum dos dois,
    só não manda pra esse convidado).

    Só marca como enviado pro dono DEPOIS de confirmar que enviou (mesmo motivo
    do aviso ao convidado, ver _avisar_convidados_confirmados): uma falha do
    Telegram (rede, fora do ar) não pode queimar a tentativa pra sempre — o
    próximo ciclo (a cada ~2min, até o evento começar) tenta de novo."""
    eventos = ag.listar_eventos(pool, conta_id, agora, agora + timedelta(minutes=antes_min))
    n = 0
    for ev in eventos:
        h = ev["inicio"].astimezone(ag.BRT).strftime("%H:%M")
        faltam = max(0, int((ev["inicio"] - agora).total_seconds() // 60))
        chave = f"evt:{ev['id']}"
        if not _ja_avisado(pool, conta_id, "aviso", chave):
            loc = f"\n📍 {ev['local']}" if ev.get("local") else ""
            txt = f"⏰ *Daqui a pouco* (em ~{faltam} min): *{ev['titulo']}* às {h}.{loc}"
            ok = notificar.enviar_para_dono(pool, conta_id, txt)
            cv.registrar_mensagem(pool, conta_id, ev["id"], None, "lembrete", "telegram",
                                  ok, None if ok else "falha_envio")
            if ok:
                _primeira_vez(pool, conta_id, "aviso", chave)
                n += 1
        if avisar_convidados:
            n += _avisar_convidados_confirmados(pool, conta_id, ev, h, faltam, agora)
    return n


def _ja_avisado(pool, conta_id: int, tipo: str, chave: str) -> bool:
    with pool.connection() as c:
        r = c.execute(
            "select 1 from lembretes_enviados where conta_id=%s and tipo=%s and chave=%s",
            (conta_id, tipo, chave)).fetchone()
        return r is not None


def _avisar_convidados_confirmados(pool, conta_id: int, ev: dict, hora: str,
                                   faltam: int, agora) -> int:
    """Manda o mesmo 'tá chegando a hora' pra quem CONFIRMOU presença nesse evento
    — dedup por convidado (não pelo evento, já consumido pelo aviso do dono).

    Só marca como enviado DEPOIS de confirmar que enviou — uma falha (Twilio
    fora do ar, sem template pra fora da janela etc.) não pode "queimar" a
    tentativa pra sempre; o próximo ciclo (a cada ~2min, até o evento começar)
    tenta de novo. rodar() já serializa por advisory lock, então não corre risco
    de mandar em dobro entre checar e marcar."""
    n = 0
    convidados = cv.por_evento(pool, conta_id, [ev["id"]]).get(ev["id"], [])
    for g in convidados:
        if g["status"] != "confirmado" or not (g.get("contato") or "").strip():
            continue
        chave = f"evt:{ev['id']}:conv:{g['id']}"
        try:
            if _ja_avisado(pool, conta_id, "aviso_convidado", chave):
                continue
            r = cv.avisar_convidado_confirmado(pool, conta_id, ev["id"], g["id"],
                                               g["contato"], g.get("nome"),
                                               ev["titulo"], hora, faltam,
                                               g.get("respondido_em"), g.get("respondido_canal"),
                                               agora)
            if r.get("ok"):
                _primeira_vez(pool, conta_id, "aviso_convidado", chave)
                n += 1
        except Exception:  # noqa: BLE001
            # um convidado com problema (ex.: dedup falhando por algum motivo
            # inesperado) NÃO pode derrubar o ciclo de lembretes das outras
            # contas — _rodar() varre todas numa passada só, sem isolamento.
            _log.warning("aviso ao convidado %s (evt %s) falhou", g.get("id"), ev["id"], exc_info=True)
    return n
