"""Cliente da API da Explorium (Vibe Prospecting) — dados B2B de médio/grande porte.

Fase 1: só o `businesses/match` (que o dono validou por curl) + um _post genérico,
pra testar a conexão dentro do ZAQ e ver o formato real da resposta. Com isso na
mão, a fase 2 pluga enrich de firmografia + contato do decisor sem chutar payload.

A chave vem de EXPLORIUM_API_KEY (no Render) — NUNCA no código. Header: `api_key`.
"""
from __future__ import annotations

import logging
import os

import httpx

_log = logging.getLogger("openclaw.explorium")
_BASE = "https://api.explorium.ai/v1"
_TIMEOUT = 25


def tem_credenciais() -> bool:
    return bool((os.environ.get("EXPLORIUM_API_KEY") or "").strip())


def _headers() -> dict:
    return {"api_key": (os.environ.get("EXPLORIUM_API_KEY") or "").strip(),
            "Content-Type": "application/json"}


def _post(caminho: str, payload: dict) -> dict:
    """POST best-effort. Devolve {ok, status, data} ou {ok:False, erro}."""
    try:
        r = httpx.post(_BASE + caminho, headers=_headers(), json=payload, timeout=_TIMEOUT)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "erro": f"{type(e).__name__}: {e}"}
    ct = r.headers.get("content-type", "")
    corpo = r.json() if ct.startswith("application/json") else r.text[:2000]
    return {"ok": r.status_code < 300, "status": r.status_code, "data": corpo}


def match_business(nome: str, dominio: str = "") -> dict:
    """Acha o business_id da empresa (por nome + domínio). Endpoint validado pelo dono."""
    b: dict = {"name": (nome or "").strip()}
    if (dominio or "").strip():
        b["domain"] = dominio.strip()
    return _post("/businesses/match", {"businesses_to_match": [b]})
