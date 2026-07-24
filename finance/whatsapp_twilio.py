"""Adaptador de WhatsApp via Twilio (canal do inbox omnichannel).

Tudo tolerante a falta de config: sem as env (TWILIO_ACCOUNT_SID/AUTH_TOKEN/
WHATSAPP_FROM) o adaptador fica inerte (no-op) e nada quebra. O SDK `twilio` é
importado tarde (lazy) — o módulo carrega mesmo sem ele instalado.

Credenciais da conta Twilio são GLOBAIS (env); o NÚMERO de cada empresa é por
conta (banco: canais_config), porque o env TWILIO_WHATSAPP_FROM é único e já é do
Zaq. O inbound roteia pelo número que recebeu (To → conta).

Env:
- TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN  — credenciais (no Render, nunca no código)
- APP_URL                                 — URL pública (valida a assinatura do webhook)
"""
from __future__ import annotations

import os


def _cfg() -> dict | None:
    sid = os.environ.get("TWILIO_ACCOUNT_SID")
    tok = os.environ.get("TWILIO_AUTH_TOKEN")
    if not (sid and tok):
        return None
    return {"sid": sid, "token": tok}


def configurado() -> bool:
    """Credenciais globais presentes (o número em si é por empresa, no banco)."""
    return _cfg() is not None


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


def normalizar_from(numero: str) -> str:
    """Formato canônico do número da empresa pra guardar/usar como 'from' (whatsapp:+DDI...)."""
    d = _so_digitos(numero)
    return ("whatsapp:+" + d) if d else ""


def enviar_texto(remetente: str, numero: str, corpo: str) -> dict:
    """Envia texto livre (dentro da janela de 24h) a partir do NÚMERO da empresa
    (`remetente`, ex 'whatsapp:+17602847678'). Fora da janela o Twilio recusa — aí
    é preciso template (próxima etapa)."""
    cfg = _cfg()
    if not cfg:
        return {"ok": False, "erro": "nao_configurado"}
    if not remetente:
        return {"ok": False, "erro": "sem_numero_empresa"}
    to = _wa_addr(numero)
    if not to:
        return {"ok": False, "erro": "numero_invalido"}
    try:
        from twilio.rest import Client
        msg = Client(cfg["sid"], cfg["token"]).messages.create(
            from_=remetente, to=to, body=(corpo or "")[:1500])
        return {"ok": True, "sid": msg.sid}
    except Exception as e:  # noqa: BLE001
        # Provedor pago: avisa o admin se a falha for sistemica (sem saldo Twilio,
        # SID/token invalido, etc). Best-effort — nao muda o retorno.
        from core.falhas import avaliar_falha_provedor
        avaliar_falha_provedor(e, servico="Twilio (WhatsApp)")
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
