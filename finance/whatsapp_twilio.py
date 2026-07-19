"""Adaptador de WhatsApp via Twilio (canal do inbox omnichannel).

Tudo tolerante a falta de config: sem as env (TWILIO_ACCOUNT_SID/AUTH_TOKEN/
WHATSAPP_FROM) o adaptador fica inerte (no-op) e nada quebra. O SDK `twilio` é
importado tarde (lazy) — o módulo carrega mesmo sem ele instalado.

Env:
- TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN  — credenciais (no Render, nunca no código)
- TWILIO_WHATSAPP_FROM                    — ex: 'whatsapp:+17602847678'
- WHATSAPP_CONTA_ID                       — conta dona do número (roteia o inbound)
- APP_URL                                 — URL pública (valida a assinatura do webhook)
"""
from __future__ import annotations

import os


def _cfg() -> dict | None:
    sid = os.environ.get("TWILIO_ACCOUNT_SID")
    tok = os.environ.get("TWILIO_AUTH_TOKEN")
    frm = os.environ.get("TWILIO_WHATSAPP_FROM")
    if not (sid and tok and frm):
        return None
    return {"sid": sid, "token": tok, "from": frm.strip()}


def configurado() -> bool:
    return _cfg() is not None


def conta_dona() -> int | None:
    """Conta (tenant) dona do número — pra rotear as mensagens que chegam."""
    v = os.environ.get("WHATSAPP_CONTA_ID")
    return int(v) if v and v.isdigit() else None


def _so_digitos(s: str) -> str:
    return "".join(ch for ch in (s or "") if ch.isdigit())


def _wa_addr(numero: str) -> str:
    """Normaliza um telefone pra 'whatsapp:+55DDDNUMERO' (assume BR se sem DDI)."""
    d = _so_digitos(numero)
    if not d:
        return ""
    if not d.startswith("55") and len(d) <= 11:
        d = "55" + d
    return "whatsapp:+" + d


def enviar_texto(numero: str, corpo: str) -> dict:
    """Envia texto livre (dentro da janela de 24h). Fora da janela o Twilio recusa —
    aí é preciso template (fica pra próxima etapa)."""
    cfg = _cfg()
    if not cfg:
        return {"ok": False, "erro": "nao_configurado"}
    to = _wa_addr(numero)
    if not to:
        return {"ok": False, "erro": "numero_invalido"}
    try:
        from twilio.rest import Client
        msg = Client(cfg["sid"], cfg["token"]).messages.create(
            from_=cfg["from"], to=to, body=(corpo or "")[:1500])
        return {"ok": True, "sid": msg.sid}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "erro": str(e)[:200]}


def validar_assinatura(url: str, params: dict, assinatura: str) -> bool:
    """Valida o X-Twilio-Signature (RequestValidator do SDK)."""
    cfg = _cfg()
    if not cfg:
        return False
    try:
        from twilio.request_validator import RequestValidator
        return RequestValidator(cfg["token"]).validate(url, params, assinatura or "")
    except Exception:  # noqa: BLE001
        return False


def url_webhook() -> str:
    """URL pública que o Twilio chama — precisa bater exatamente pra assinar."""
    base = (os.environ.get("APP_URL") or "").rstrip("/")
    return base + "/webhooks/twilio"
