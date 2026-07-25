#!/usr/bin/env python3
"""Gerencia o webhook do Telegram (registrar / remover / consultar).

O web/app.py ja' registra o webhook sozinho no startup SE TELEGRAM_WEBHOOK_URL
estiver setada. Este script e' pra operar na mao: conferir o estado atual,
apontar pra outra URL, ou VOLTAR pro polling (delete) durante um teste.

Precisa falar com api.telegram.org - rode no shell do Render (produção) ou
local; em ambiente com egresso restrito esse host pode estar bloqueado.

    export TELEGRAM_TOKEN=123:abc
    export TELEGRAM_WEBHOOK_SECRET=...              # opcional, mas recomendado
    python -m scripts.telegram_webhook info
    python -m scripts.telegram_webhook set https://<web>.onrender.com/webhook/telegram
    python -m scripts.telegram_webhook delete       # volta a permitir polling
"""
from __future__ import annotations

import json
import os
import sys


def _api(metodo: str, **params):
    import requests
    token = os.environ.get("TELEGRAM_TOKEN")
    if not token:
        sys.exit("TELEGRAM_TOKEN nao definida.")
    ca = os.environ.get("REQUESTS_CA_BUNDLE") or "/root/.ccr/ca-bundle.crt"
    verify = ca if os.path.exists(ca) else True
    url = f"https://api.telegram.org/bot{token}/{metodo}"
    try:
        r = requests.post(url, json=params, timeout=30, verify=verify)
    except requests.exceptions.ProxyError as e:
        sys.exit(f"[REDE BLOQUEADA] api.telegram.org recusado pelo proxy "
                 f"(politica de egresso). Rode no shell do Render.\n{e}")
    except requests.exceptions.RequestException as e:
        sys.exit(f"[REDE] falha ao falar com o Telegram: {e}")
    dados = r.json()
    if not dados.get("ok"):
        sys.exit(f"[TELEGRAM] {dados}")
    return dados.get("result")


def cmd_info(_argv):
    info = _api("getWebhookInfo")
    print(json.dumps(info, indent=2, ensure_ascii=False))
    if info.get("url"):
        print(f"\n>> Entrega ATUAL: WEBHOOK ({info['url']})")
        if info.get("pending_update_count"):
            print(f">> Pendentes na fila: {info['pending_update_count']}")
    else:
        print("\n>> Entrega ATUAL: POLLING (sem webhook) - o worker recebe.")


def cmd_set(argv):
    if not argv:
        sys.exit("uso: set <url>  (ex: https://<web>.onrender.com/webhook/telegram)")
    url = argv[0]
    secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET") or None
    _api("setWebhook", url=url, secret_token=secret,
         allowed_updates=["message", "edited_message", "callback_query"])
    print(f"Webhook registrado: {url}")
    print("A partir de agora o Telegram entrega no WEB. O worker de polling vai "
          "receber 409 (inofensivo) ate' ser aposentado.")


def cmd_delete(_argv):
    _api("deleteWebhook", drop_pending_updates=False)
    print("Webhook removido. O Telegram volta a permitir POLLING (worker).")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cmds = {"info": cmd_info, "set": cmd_set, "delete": cmd_delete}
    if not argv or argv[0] not in cmds:
        sys.exit(f"comandos: {', '.join(cmds)}")
    cmds[argv[0]](argv[1:])


if __name__ == "__main__":
    main()
