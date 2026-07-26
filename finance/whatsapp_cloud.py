"""Adaptador de WhatsApp via Cloud API OFICIAL da Meta (número próprio do cliente).

Sem BSP/Twilio: o cliente registra o PRÓPRIO número na Meta (WhatsApp Business
Platform) e a gente envia/recebe direto pela Graph API. Tudo por conta (banco:
canais_config): `wa_phone_id` = phone_number_id do número na Meta e `token` = access
token (System User, permanente). Só stdlib (urllib), tolerante a falta de config.

Recebimento chega no MESMO webhook /webhooks/meta (object='whatsapp_business_account'),
com a assinatura HMAC do app (META_APP_SECRET) — ver finance/meta_msg.py.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

_log = logging.getLogger("openclaw.wacloud")
_GRAPH = "https://graph.facebook.com/v19.0"
_TIMEOUT = 15


def _so_digitos(s: str) -> str:
    return "".join(ch for ch in (s or "") if ch.isdigit())


def _msisdn(numero: str) -> str:
    """Número do destino em dígitos com DDI (E.164 sem '+'); assume BR se sem DDI."""
    d = _so_digitos(numero)
    if not d:
        return ""
    if not d.startswith("55") and len(d) <= 11:
        d = "55" + d
    return d


def configurado(wa_phone_id: str, token: str) -> bool:
    return bool(wa_phone_id and token)


def enviar_texto(wa_phone_id: str, token: str, numero: str, corpo: str) -> dict:
    """Envia texto livre (janela de 24h) pelo número do cliente via Cloud API.
    `wa_phone_id` = phone_number_id na Meta; `token` = access token da conta."""
    if not wa_phone_id or not token:
        return {"ok": False, "erro": "nao_configurado"}
    to = _msisdn(numero)
    if not to:
        return {"ok": False, "erro": "numero_invalido"}
    payload = {"messaging_product": "whatsapp", "to": to,
               "type": "text", "text": {"body": (corpo or "")[:4000]}}
    url = f"{_GRAPH}/{urllib.parse.quote(str(wa_phone_id))}/messages"
    try:
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + token}, method="POST")
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            d = json.loads(r.read().decode("utf-8") or "{}")
        sid = ""
        try:
            sid = (d.get("messages") or [{}])[0].get("id") or ""
        except Exception:  # noqa: BLE001
            pass
        return {"ok": True, "sid": sid}
    except urllib.error.HTTPError as e:  # noqa: BLE001
        try:
            det = e.read().decode("utf-8")[:200]
        except Exception:  # noqa: BLE001
            det = str(e)
        _log.info("wacloud.enviar HTTP %s: %s", e.code, det)
        return {"ok": False, "erro": det}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "erro": str(e)[:200]}
