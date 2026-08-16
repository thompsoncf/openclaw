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

# Falhas que NÃO mudam sozinhas dentro da janela do evento (~30 min): faltou
# configuração (template, número, canal). Retentar a cada 2 min só repete o mesmo
# erro e enche o histórico de linhas idênticas — então marcamos como resolvido e
# paramos, guardando o motivo. O botão "Reenviar" do Histórico de envios continua
# lá pra tentar de novo depois de arrumar a configuração.
# Qualquer outro erro (timeout, http_5xx, exceção de rede) é tratado como
# TRANSITÓRIO e continua retentando — foi pra isso que o dedup deixou de ser
# gravado antes do envio.
_FALHA_PERMANENTE = {
    "sem_numero", "sem_template", "fora_da_janela_sem_template",
    "provedor_sem_template", "numero_invalido", "nao_configurado",
    "sem_numero_empresa", "convite_nao_encontrado", "sem_telegram",
}


def rodar(pool, agora=None) -> dict:
    """Ponto de entrada do ticker. Nunca levanta — só registra e segue."""
    try:
        return _rodar(pool, agora or ag.agora_brt())
    except Exception as e:  # noqa: BLE001
        _log.info("lembretes.rodar falhou: %s: %s", type(e).__name__, e)
        return {"resumo": 0, "aviso": 0}


def _expirar_pre_reservas(pool, agora) -> int:
    """Libera as datas cujo prazo do sinal venceu e avisa o dono.

    Roda pra TODA conta, não só as que ligaram lembrete: a data segurada trava o
    calendário de quem vende data, e soltar não é um lembrete — é a regra do
    negócio acontecendo. Best-effort: o aviso que falha não impede a liberação.
    """
    try:
        expiradas = ag.expirar_pre_reservas(pool, agora)
    except Exception as e:  # noqa: BLE001
        _log.info("lembretes: expirar pré-reservas falhou: %s: %s", type(e).__name__, e)
        return 0
    for ev in expiradas:
        try:
            quando = ag.fmt_hora(ev)
            notificar.enviar_para_dono(
                pool, ev["conta_id"],
                f"📅 A pré-reserva de *{ev['titulo']}* — {quando} venceu: o sinal não foi "
                "confirmado no prazo e a data está livre de novo. "
                "Se o cliente aparecer, dá pra reabrir pelo orçamento.")
        except Exception:  # noqa: BLE001 — aviso nunca segura a liberação da data
            _log.info("lembretes: não deu pra avisar da pré-reserva %s", ev.get("id"))
    return len(expiradas)


def _rodar(pool, agora) -> dict:
    n_res = n_avi = 0
    with pool.connection() as lockc:
        if not lockc.execute("select pg_try_advisory_lock(%s)", (_LOCK,)).fetchone()[0]:
            return {"resumo": 0, "aviso": 0}
        try:
            _expirar_pre_reservas(pool, agora)
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
            # sem Telegram vinculado nunca vai dar certo; falha de rede pode dar
            # no próximo ciclo (só consulta o vínculo quando falha, não sempre).
            motivo = None if ok else ("falha_envio" if notificar.dono_tem_telegram(pool, conta_id)
                                      else "sem_telegram")
            cv.registrar_mensagem(pool, conta_id, ev["id"], None, "lembrete", "telegram",
                                  ok, motivo)
            if ok:
                _primeira_vez(pool, conta_id, "aviso", chave)
                n += 1
            elif motivo in _FALHA_PERMANENTE:
                _primeira_vez(pool, conta_id, "aviso", chave)
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

    Só marca como enviado DEPOIS de confirmar que enviou — uma falha TRANSITÓRIA
    (Twilio fora do ar, timeout) não pode "queimar" a tentativa pra sempre; o
    próximo ciclo (a cada ~2min, até o evento começar) tenta de novo. Já uma
    falha PERMANENTE (ver _FALHA_PERMANENTE: faltou template/número/canal) marca
    e para — retentar não muda nada e só duplica linha no histórico.
    rodar() já serializa por advisory lock, então não corre risco de mandar em
    dobro entre checar e marcar."""
    n = 0
    convidados = cv.por_evento(pool, conta_id, [ev["id"]]).get(ev["id"], [])
    for g in convidados:
        if g["status"] != "confirmado" or not (g.get("contato") or "").strip():
            continue
        chave = f"evt:{ev['id']}:conv:{g['id']}"
        try:
            if _ja_avisado(pool, conta_id, "aviso_convidado", chave):
                continue
            r = cv.avisar_convidado_confirmado(pool, conta_id, ev, g, hora, faltam, agora)
            if r.get("ok"):
                _primeira_vez(pool, conta_id, "aviso_convidado", chave)
                n += 1
            elif r.get("erro") in _FALHA_PERMANENTE:
                _primeira_vez(pool, conta_id, "aviso_convidado", chave)
        except Exception:  # noqa: BLE001
            # um convidado com problema (ex.: dedup falhando por algum motivo
            # inesperado) NÃO pode derrubar o ciclo de lembretes das outras
            # contas — _rodar() varre todas numa passada só, sem isolamento.
            _log.warning("aviso ao convidado %s (evt %s) falhou", g.get("id"), ev["id"], exc_info=True)
    return n
