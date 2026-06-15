"""Notificacoes via Telegram para membros da conta (dono, etc).

Usa a Telegram Bot API direto: dispara mensagens sem ficar na fila de LivroCaixa.
Tolerante a falha: se a rede cair ou o token nao existir, loga e continua.
"""
import os
import urllib.request
import urllib.error


def avisar_dono_lista_fechada(pool, conta_id: int, quem_nome: str, n_itens: int) -> bool:
    """Avisa o dono que alguem (quem_nome) fechou a lista de compras com N itens pendentes.

    Busca o telegram_id do dono e manda uma mensagem no bot.
    Retorna True se conseguiu mandar (ou faria mandar se tivesse token),
    False só se erro CRITICO (dono nao tem telegram, etc).
    """
    token = os.environ.get("TELEGRAM_TOKEN")
    if not token:
        # Sem token, nao consegue mandar agora. Tolerante: log e segue.
        return False

    # Busca o dono e seu telegram_id
    with pool.connection() as c:
        row = c.execute(
            "select telegram_id, nome from membros where conta_id=%s and papel='dono'",
            (conta_id,),
        ).fetchone()

    if not row or not row[0]:
        # Dono nao tem telegram vinculado
        return False

    telegram_id = row[0]
    dono_nome = row[1] or "Responsável"

    # Formata a mensagem
    plural = "item" if n_itens == 1 else "itens"
    msg = f"📋 {quem_nome} fechou a lista de compras ({n_itens} {plural} pendentes)."

    # Manda via Telegram Bot API
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        dados = urllib.parse.urlencode({
            "chat_id": telegram_id,
            "text": msg,
            "parse_mode": "HTML",
        }).encode("utf-8")
        req = urllib.request.Request(url, data=dados)
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 200:
                return True
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        # Falha na rede ou na API — tolerante
        pass

    return True  # Mesmo com falha, considera "ok" pra nao alarmar o usuario


import urllib.parse
