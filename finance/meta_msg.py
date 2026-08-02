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
_GRAPH = "https://graph.facebook.com/v19.0"          # Messenger + Instagram via login do Facebook (token EAA)
_GRAPH_IG = "https://graph.instagram.com/v21.0"      # Instagram via login do Instagram (token IGAA)
_TIMEOUT = 15


def app_secret() -> str | None:
    # .strip(): tolera espaço/quebra de linha coladas por acidente no env (causa
    # clássica de assinatura do webhook falhar mesmo com o secret "certo").
    secs = app_secrets()
    return secs[0] if secs else None


def app_secrets() -> list[str]:
    """Um ou MAIS app secrets. A Meta assina cada webhook com o secret do app que
    gerou o evento — e o Instagram (login do Instagram) tem um secret PRÓPRIO,
    diferente do secret do Facebook. Então aceitamos vários: META_APP_SECRET pode
    trazer uma lista separada por vírgula, e META_APP_SECRET_2/META_APP_SECRET_IG
    entram como extras. Valida se bater com QUALQUER um."""
    brutos = [
        os.environ.get("META_APP_SECRET") or "",
        os.environ.get("META_APP_SECRET_2") or "",
        os.environ.get("META_APP_SECRET_IG") or "",
    ]
    out, vistos = [], set()
    for b in brutos:
        for parte in b.split(","):
            s = parte.strip()
            if s and s not in vistos:
                vistos.add(s)
                out.append(s)
    return out


def verify_token() -> str | None:
    return (os.environ.get("META_VERIFY_TOKEN") or "").strip() or None


def configurado() -> bool:
    """Credenciais de APP presentes (o token de página é por conta, no banco)."""
    return bool(app_secret() and verify_token())


def verificar_challenge(mode: str, token: str, challenge: str):
    """GET do webhook: a Meta manda hub.mode/hub.verify_token/hub.challenge."""
    if mode == "subscribe" and token and verify_token() and token == verify_token():
        return challenge
    return None


def validar_assinatura(body: bytes, header_sig: str) -> bool:
    """Valida o X-Hub-Signature-256 (HMAC-SHA256 do corpo). Tenta TODOS os app
    secrets (Facebook + Instagram, se houver) — basta um bater."""
    secs = app_secrets()
    if not secs or not header_sig:
        _log.info("meta.assinatura: sem %s", "secret (META_APP_SECRET)" if not secs else "header X-Hub-Signature-256")
        return False
    calcs = []
    for sec in secs:
        try:
            esperado = "sha256=" + hmac.new(sec.encode(), body, hashlib.sha256).hexdigest()
        except Exception:  # noqa: BLE001
            continue
        if hmac.compare_digest(esperado, header_sig):
            return True
        calcs.append(esperado[:20])
    # diagnóstico (não vaza o secret): quantos secrets tentados + prefixos calculados
    _log.info("meta.assinatura FALHOU: n_secrets=%d body_len=%d recv=%s calcs=%s",
              len(secs), len(body), (header_sig or "")[:20], calcs)
    return False


def resolver_conta_ig(token: str) -> dict:
    """Descobre o ID da conta (user_id) e o @username a partir de um token IGAA,
    chamando graph.instagram.com/me. Roda no servidor (que tem internet). Devolve
    {ok, user_id, username} ou {ok:False, erro}."""
    t = (token or "").strip()
    if not t:
        return {"ok": False, "erro": "sem_token"}
    url = _GRAPH_IG + "/me?fields=user_id,username&access_token=" + urllib.parse.quote(t)
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="GET"), timeout=_TIMEOUT) as r:
            d = json.loads(r.read().decode("utf-8") or "{}")
        uid = str(d.get("user_id") or d.get("id") or "").strip()
        if not uid:
            return {"ok": False, "erro": "a Meta não devolveu user_id"}
        return {"ok": True, "user_id": uid, "username": d.get("username") or ""}
    except urllib.error.HTTPError as e:  # noqa: BLE001
        try:
            det = e.read().decode("utf-8")[:200]
        except Exception:  # noqa: BLE001
            det = str(e)
        return {"ok": False, "erro": det}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "erro": str(e)[:200]}


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
    # token IGAA = Instagram Login → graph.instagram.com; senão (EAA) → graph.facebook.com (Página)
    base = _GRAPH_IG if (page_token or "").startswith("IGAA") else _GRAPH
    url = base + "/me/messages?access_token=" + urllib.parse.quote(page_token)
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
    [{plataforma, conta_ident, sender, texto, nome?}] — ignora ecos e não-texto.
    Cobre Messenger/Instagram (object 'page'/'instagram') e WhatsApp Cloud API
    (object 'whatsapp_business_account')."""
    obj = payload.get("object")
    if obj == "whatsapp_business_account":
        return _parse_whatsapp(payload)
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


def _parse_whatsapp(payload: dict) -> list[dict]:
    """WhatsApp Cloud API: entry[].changes[].value.messages[]. Roteia pelo
    phone_number_id (metadata) e ignora status (delivered/read) e não-texto."""
    saida = []
    for entry in payload.get("entry", []) or []:
        for ch in (entry.get("changes") or []):
            val = ch.get("value") or {}
            phone_id = str(((val.get("metadata") or {}).get("phone_number_id")) or "")
            nomes = {}
            for ct in (val.get("contacts") or []):
                nomes[str(ct.get("wa_id") or "")] = ((ct.get("profile") or {}).get("name") or "").strip()
            for msg in (val.get("messages") or []):
                if msg.get("type") != "text":
                    continue                   # ignora mídia/áudio/etc por enquanto
                texto = ((msg.get("text") or {}).get("body") or "").strip()
                sender = str(msg.get("from") or "")
                if not texto or not sender or not phone_id:
                    continue
                saida.append({"plataforma": "whatsapp", "conta_ident": phone_id,
                              "sender": sender, "texto": texto, "nome": nomes.get(sender, ""),
                              "sid": str(msg.get("id") or "") or None})
    return saida
