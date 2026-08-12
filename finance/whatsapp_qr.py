"""Cliente do serviço de WhatsApp por QR Code (services/wa-qr, Node/Baileys).

O app só conversa por HTTP com o serviço externo — nada de sessão aqui. Tolerante:
sem WA_QR_SERVICE_URL/WA_QR_SHARED_SECRET, tudo vira no-op (o painel mostra "a
configurar" e o envio devolve erro amigável). Só stdlib.

Env:
- WA_QR_SERVICE_URL   — URL pública do serviço Node (ex.: https://...onrender.com)
- WA_QR_SHARED_SECRET — segredo compartilhado (header x-wa-secret)
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

_TIMEOUT = 20


def _base() -> str:
    return (os.environ.get("WA_QR_SERVICE_URL") or "").rstrip("/")


def _segredo() -> str:
    return os.environ.get("WA_QR_SHARED_SECRET") or ""


def configurado() -> bool:
    """O serviço de QR está ligado (env presentes)?"""
    return bool(_base() and _segredo())


def _req(metodo: str, caminho: str, corpo: dict | None = None,
         timeout: int | None = None) -> dict:
    if not configurado():
        return {"ok": False, "erro": "qr_indisponivel"}
    url = _base() + caminho
    data = json.dumps(corpo).encode("utf-8") if corpo is not None else None
    headers = {"x-wa-secret": _segredo()}
    if data is not None:
        headers["content-type"] = "application/json"
    try:
        req = urllib.request.Request(url, data=data, headers=headers, method=metodo)
        with urllib.request.urlopen(req, timeout=timeout or _TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:  # noqa: BLE001
        try:
            det = e.read().decode("utf-8")[:180]
        except Exception:  # noqa: BLE001
            det = str(e)
        return {"ok": False, "erro": f"http_{e.code}", "det": det}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "erro": str(e)[:180]}


def iniciar(conta_id: int) -> dict:
    """Liga a sessão e devolve {status, qr?} (qr = data URL pra exibir)."""
    return _req("POST", f"/session/{conta_id}/iniciar")


def status(conta_id: int) -> dict:
    """{status: conectado|aguardando_qr|reconectando|desconectado, qr?}."""
    return _req("GET", f"/session/{conta_id}/status")


def enviar_texto(conta_id: int, numero: str, texto: str) -> dict:
    """Manda um texto pelo número conectado por QR daquela empresa."""
    r = _req("POST", f"/session/{conta_id}/enviar", {"numero": numero, "texto": texto})
    # normaliza pro formato dos outros adaptadores ({ok, sid?/erro})
    if r.get("ok"):
        return {"ok": True, "sid": r.get("id") or ""}
    return {"ok": False, "erro": r.get("erro") or "falha"}


def sair(conta_id: int) -> dict:
    """Desconecta e apaga a sessão daquela empresa.

    Prazo maior que o padrão: o serviço só responde depois de avisar o celular,
    apagar a credencial (que são milhares de linhas em wa_qr_auth) e limpar o
    histórico de conversa. Com os 20s padrão isso estourava em conta grande, e o
    painel dizia "Desconectado" com a sessão inteira ainda de pé."""
    return _req("POST", f"/session/{conta_id}/sair", timeout=60)
