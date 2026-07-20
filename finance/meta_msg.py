"""Adaptador Messenger + Instagram via Graph API da Meta (um webhook /webhooks/meta).

Credenciais de APP são GLOBAIS (env): META_APP_SECRET (assina o webhook) e
META_VERIFY_TOKEN (verificação do webhook). O TOKEN de página é POR CONTA (banco:
canais_config.token) — cada empresa tem a sua Página/IG. O inbound roteia pelo id
que recebeu (page id → messenger; ig id → instagram). Sem SDK: só stdlib (urllib).

Env:
- META_APP_SECRET     — segredo do app (assinatura X-Hub-Signature-256)
- META_VERIFY_TOKEN   — token que você define e cola no painel da Meta (verificação)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request

_log = logging.getLogger("openclaw.meta")
_GRAPH = "https://graph.facebook.com/v19.0"
_TIMEOUT = 15


def app_secret() -> str | None:
    return os.environ.get("META_APP_SECRET")


def verify_token() -> str | None:
    return os.environ.get("META_VERIFY_TOKEN")


def configurado() -> bool:
    """Credenciais de APP presentes (o token de página é por conta, no banco)."""
    return bool(app_secret() and verify_token())


def verificar_challenge(mode: str, token: str, challenge: str):
    """GET do webhook: a Meta manda hub.mode/hub.verify_token/hub.challenge."""
    if mode == "subscribe" and token and verify_token() and token == verify_token():
        return challenge
    return None


def validar_assinatura(body: bytes, header_sig: str) -> bool:
    """Valida o X-Hub-Signature-256 (HMAC-SHA256 do corpo com o app secret)."""
    sec = app_secret()
    if not sec or not header_sig:
        return False
    try:
        esperado = "sha256=" + hmac.new(sec.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(esperado, header_sig)
    except Exception:  # noqa: BLE001
        return False


def enviar(page_token: str, destino_id: str, texto: str, plataforma: str = "messenger") -> dict:
    """Envia texto (dentro da janela de 24h) via Graph API, usando o Page Access
    Token da empresa. `destino_id` = PSID (Messenger) ou IGSID (Instagram)."""
    if not page_token:
        return {"ok": False, "erro": "sem_token"}
    if not destino_id:
        return {"ok": False, "erro": "sem_destino"}
    payload = {"recipient": {"id": str(destino_id)},
               "messaging_type": "RESPONSE",
               "message": {"text": (texto or "")[:1900]}}
    url = _GRAPH + "/me/messages?access_token=" + urllib.parse.quote(page_token)
    try:
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            d = json.loads(r.read().decode("utf-8") or "{}")
        return {"ok": True, "sid": d.get("message_id") or d.get("recipient_id")}
    except urllib.error.HTTPError as e:  # noqa: BLE001
        try:
            det = e.read().decode("utf-8")[:200]
        except Exception:  # noqa: BLE001
            det = str(e)
        _log.info("meta.enviar HTTP %s: %s", e.code, det)
        return {"ok": False, "erro": det}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "erro": str(e)[:200]}


def parse_eventos(payload: dict) -> list[dict]:
    """Extrai as mensagens de texto recebidas do payload do webhook. Devolve
    [{plataforma, conta_ident, sender, texto}] — ignora ecos e não-texto."""
    obj = payload.get("object")
    plataforma = "instagram" if obj == "instagram" else "messenger"
    saida = []
    for entry in payload.get("entry", []) or []:
        recv_id = str(entry.get("id") or "")
        for ev in (entry.get("messaging") or []):
            msg = ev.get("message") or {}
            if msg.get("is_echo"):
                continue                       # eco da nossa própria mensagem
            texto = (msg.get("text") or "").strip()
            sender = str((ev.get("sender") or {}).get("id") or "")
            if not texto or not sender:
                continue
            saida.append({"plataforma": plataforma, "conta_ident": recv_id,
                          "sender": sender, "texto": texto})
    return saida
