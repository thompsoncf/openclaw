"""A escolha com toque no WhatsApp do Zaq (Twilio): lista ou botões.

O WhatsApp só mostra lista e botões por CONTEÚDO do Twilio (Content API):
  - `twilio/quick-reply`: até 3 botões, título até 20 letras;
  - `twilio/list-picker`: um botão que abre até 10 linhas (título até 24 letras).
Dentro da conversa (a pessoa falou nas últimas 24 h — e quem acabou de mandar a
foto da nota falou), esse conteúdo NÃO precisa de aprovação da Meta.

Cada conjunto de opções vira um conteúdo no Twilio, criado uma vez e guardado
pelo hash em `config_app` — a mesma lista de obras manda o mesmo conteúdo, sem
criar um novo a cada nota.

Nunca levanta: devolve False, e quem chama manda as opções escritas.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os

import httpx

_log = logging.getLogger("openclaw.whatsapp_interativo")
_URL = "https://content.twilio.com/v1/Content"


def _tipos(escolha: dict) -> dict:
    """O corpo do conteúdo: botões quando cabem (até 3, título até 20), senão lista."""
    ops = escolha["opcoes"]
    if len(ops) <= 3 and all(len(o["titulo"]) <= 20 for o in ops):
        return {"twilio/quick-reply": {
            "body": escolha["texto"],
            "actions": [{"title": o["titulo"], "id": f"op{n}"} for n, o in enumerate(ops, start=1)]}}
    return {"twilio/list-picker": {
        "body": escolha["texto"], "button": escolha["botao"][:20],
        "items": [{"item": o["titulo"][:24], "id": f"op{n}",
                   "description": (o.get("descricao") or "")[:72]} for n, o in enumerate(ops, start=1)]}}


def chave(escolha: dict) -> str:
    bruto = json.dumps(_tipos(escolha), sort_keys=True, ensure_ascii=False)
    return "tw_escolha_" + hashlib.sha1(bruto.encode("utf-8")).hexdigest()[:20]


def _conteudo(pool, escolha: dict, auth, post=None) -> str | None:
    """O SID do conteúdo: o guardado, ou cria e guarda."""
    from . import config_app
    k = chave(escolha)
    sid = config_app.get_config(pool, k)
    if sid:
        return sid
    post = post or (lambda url, payload: httpx.post(url, json=payload, auth=auth, timeout=15))
    r = post(_URL, {"friendly_name": k, "language": "pt_BR", "types": _tipos(escolha)})
    if getattr(r, "status_code", 0) not in (200, 201):
        _log.warning("whatsapp_interativo: o Twilio recusou o conteúdo (%s): %s",
                     getattr(r, "status_code", "?"), (getattr(r, "text", "") or "")[:300])
        return None
    sid = r.json().get("sid")
    if sid:
        config_app.set_config(pool, k, sid)
    return sid


def enviar(pool, to: str, escolha: dict, *, post=None, criar_mensagem=None) -> bool:
    """Manda a lista (ou os botões) pra `to` ('whatsapp:+55...'). True se saiu.
    `post` e `criar_mensagem` são injetáveis nos testes."""
    try:
        sid_conta = os.environ.get("TWILIO_ACCOUNT_SID")
        token = os.environ.get("TWILIO_AUTH_TOKEN")
        remetente = os.environ.get("TWILIO_WHATSAPP_FROM")
        if not (sid_conta and token and remetente) and criar_mensagem is None:
            return False
        sid = _conteudo(pool, escolha, (sid_conta, token), post=post)
        if not sid:
            return False
        if criar_mensagem is None:
            from twilio.rest import Client
            criar_mensagem = Client(sid_conta, token).messages.create
        criar_mensagem(from_=remetente, to=to, content_sid=sid)
        return True
    except Exception as e:  # noqa: BLE001 — a escolha nunca derruba a resposta
        _log.warning("whatsapp_interativo: não mandei a escolha: %s", e)
        return False
