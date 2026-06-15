"""Notificacoes ativas pro dono da conta (ex: "fulano fechou a lista").

O portal (web) e o bot (worker) sao processos SEPARADOS no Render, entao o portal
nao pode usar o objeto do bot pra mandar mensagem. Em vez disso, fala direto com a
API HTTP do Telegram (api.telegram.org/bot<token>/sendMessage) usando o
TELEGRAM_TOKEN. Funciona de qualquer processo.

WhatsApp fica preparado (esqueleto) pra quando o numero oficial existir.

Tolerante a falha: se nao houver token, dono sem telegram, ou a rede falhar, a
funcao apenas registra no log e retorna False - nunca quebra o fluxo de quem
clicou o botao.
"""
import json
import logging
import os
import urllib.request

_log = logging.getLogger("openclaw.notificar")

_TG_API = "https://api.telegram.org"
_TIMEOUT = 8


def _enviar_telegram(chat_id: int, texto: str) -> bool:
    token = os.environ.get("TELEGRAM_TOKEN")
    if not token or not chat_id:
        _log.info("notificar: sem token ou chat_id (token=%s, chat=%s)",
                  bool(token), chat_id)
        return False
    url = f"{_TG_API}/bot{token}/sendMessage"
    dados = json.dumps({
        "chat_id": chat_id, "text": texto, "parse_mode": "Markdown",
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=dados, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return resp.status == 200
    except Exception as e:  # noqa: BLE001
        _log.warning("notificar: falha ao enviar telegram: %s", e)
        return False


def _telegram_id_do_dono(pool, conta_id: int) -> int | None:
    with pool.connection() as c:
        row = c.execute(
            "select telegram_id from membros "
            "where conta_id=%s and papel='dono' and telegram_id is not null",
            (conta_id,)).fetchone()
    return row[0] if row else None


def avisar_dono_lista_fechada(pool, conta_id: int, quem_nome: str,
                              n_itens: int = 0) -> bool:
    """Avisa o dono (via Telegram) que alguem fechou/terminou a lista de compras.
    A lista NAO trava - e' so' um aviso. Retorna True se conseguiu notificar.
    """
    chat = _telegram_id_do_dono(pool, conta_id)
    if not chat:
        _log.info("notificar: dono da conta %s sem telegram vinculado", conta_id)
        return False
    qtd = f" ({n_itens} {'item' if n_itens == 1 else 'itens'})" if n_itens else ""
    texto = (f"📋 *{quem_nome}* fechou a lista de compras{qtd}.\n"
             f"Dá uma olhada quando puder! Acesse o painel pra ver e comparar preços.")
    ok = _enviar_telegram(chat, texto)
    # WhatsApp: preparado pra quando o numero oficial existir.
    # if not ok: _enviar_whatsapp(...)
    return ok
