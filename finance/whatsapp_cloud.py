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


def _erro_meta(corpo: str) -> dict:
    """Tira o código de erro da Meta do corpo da recusa: {'codigo': int, 'msg': str}.

    A Graph API responde a falha com `{"error": {"code": 190, "error_subcode": ...,
    "message": "...", "error_data": {"details": "..."}}}`. Sem ler isso, o retorno
    do envio saía só com o texto cru — e quem chama não tinha COMO separar "o token
    da conta venceu" de "este número não tem WhatsApp". É a mesma leitura que o
    adaptador do Twilio já faz com `TwilioRestException.code` (whatsapp_twilio.
    _erro_provedor); sem ela a campanha ficava cega justamente no provedor que
    carrega o número próprio do cliente.

    Devolve `{}` quando não dá pra ler o código — melhor nada que um número chutado.
    """
    try:
        err = (json.loads(corpo or "{}") or {}).get("error") or {}
    except (ValueError, TypeError):
        return {}
    if not isinstance(err, dict):
        return {}
    try:
        cod = int(err.get("code"))
    except (TypeError, ValueError):
        return {}
    det = ((err.get("error_data") or {}).get("details")
           if isinstance(err.get("error_data"), dict) else "")
    msg = (err.get("message") or "") + ((" — " + det) if det else "")
    return {"codigo": cod, "msg": msg[:300] or f"erro {cod} da Meta"}


def _post(wa_phone_id: str, token: str, payload: dict, endpoint: str = "messages") -> dict:
    url = f"{_GRAPH}/{urllib.parse.quote(str(wa_phone_id))}/{endpoint}"
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
        _log.info("wacloud HTTP %s: %s", e.code, det)
        # token expirado, número banido, limite da Meta estourado: sem isso o canal
        # que carrega a conta inteira morre em silêncio (só o Twilio avisava).
        from core.falhas import avaliar_falha_provedor
        avaliar_falha_provedor(f"http_{e.code}: {det}", servico="WhatsApp Cloud API",
                               canal="whatsapp")
        return {"ok": False, "erro": det, "provedor": "cloud", **_erro_meta(det)}
    except Exception as e:  # noqa: BLE001
        from core.falhas import avaliar_falha_provedor
        avaliar_falha_provedor(e, servico="WhatsApp Cloud API", canal="whatsapp")
        # rede/timeout: não é recusa da Meta, é o servidor sem alcançar a Graph.
        # Vai como falha da CONTA (sem código) — quem chama decide, mas o alvo não
        # tem culpa nenhuma e não pode ser queimado por isso.
        return {"ok": False, "erro": str(e)[:200], "provedor": "cloud",
                "msg": str(e)[:300] or "sem resposta da Graph API"}


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
    return _post(wa_phone_id, token, payload)


def enviar_template(wa_phone_id: str, token: str, numero: str, nome_template: str,
                    variaveis: dict | None = None, lang: str = "pt_BR",
                    mmlite: bool = False) -> dict:
    """Dispara um TEMPLATE aprovado (fora da janela de 24h) via Cloud API.
    `nome_template` = nome do template aprovado na Meta (não é o Content SID do Twilio).
    `variaveis` = {"1": ..., "2": ...} viram os parâmetros do corpo, em ordem.
    `mmlite=True` roteia pela Marketing Messages Lite API (endpoint /marketing_messages):
    mesmo payload e mesmo preço, só otimiza entrega — exige a WABA habilitada em MM Lite."""
    if not wa_phone_id or not token:
        return {"ok": False, "erro": "nao_configurado"}
    if not nome_template:
        return {"ok": False, "erro": "sem_template"}
    to = _msisdn(numero)
    if not to:
        return {"ok": False, "erro": "numero_invalido"}
    params = []
    for i in range(1, 20):
        v = (variaveis or {}).get(str(i))
        if v is None:
            break
        params.append({"type": "text", "text": str(v)})
    componentes = [{"type": "body", "parameters": params}] if params else []
    payload = {"messaging_product": "whatsapp", "to": to, "type": "template",
               "template": {"name": nome_template, "language": {"code": lang},
                            "components": componentes}}
    # MM Lite = mesmo payload, só muda o endpoint (/marketing_messages)
    return _post(wa_phone_id, token, payload,
                 endpoint="marketing_messages" if mmlite else "messages")
