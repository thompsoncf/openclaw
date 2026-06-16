"""Descobre cidade/UF a partir do CEP, via BrasilAPI.

Usado no cadastro pra NAO obrigar a pessoa a escolher cidade numa lista.
Ela digita o CEP; aqui a gente descobre cidade e estado e monta a chave de
regiao que o banco de precos usa (ex: "teresina-pi").

Tolerante a falha: se o CEP for invalido, a API cair, ou a rede falhar, retorna
None - o cadastro segue sem travar (cidade fica vazia e pode ser ajustada depois).

BrasilAPI ja' esta' liberada no Render. (No sandbox de teste local ela e'
bloqueada, entao a logica e' testada com mock.)
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
import urllib.request

_log = logging.getLogger("openclaw.cep")
_TIMEOUT = 6
_BRASILAPI = "https://brasilapi.com.br/api/cep/v2/{cep}"


def _so_digitos(cep: str | None) -> str:
    return re.sub(r"\D", "", cep or "")


def _slug(cidade: str, uf: str) -> str:
    """Monta a chave de regiao no padrao do banco: 'teresina-pi' (sem acento)."""
    base = f"{cidade}-{uf}".strip().lower()
    # remove acentos
    base = unicodedata.normalize("NFKD", base).encode("ascii", "ignore").decode()
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    return base


def consultar(cep: str | None) -> dict | None:
    """Consulta o CEP e devolve {'cidade','uf','regiao','rotulo'} ou None.

    - regiao: chave normalizada pro banco de precos (ex: 'teresina-pi')
    - rotulo: nome amigavel (ex: 'Teresina - PI')
    """
    digs = _so_digitos(cep)
    if len(digs) != 8:
        return None
    try:
        req = urllib.request.Request(
            _BRASILAPI.format(cep=digs),
            headers={"User-Agent": "OpenClaw/1.0"},
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            dados = json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001 - nunca trava o cadastro
        _log.info("cep %s falhou: %s: %s", digs, type(e).__name__, e)
        return None

    cidade = (dados.get("city") or "").strip()
    uf = (dados.get("state") or "").strip().upper()
    if not cidade or not uf:
        return None
    return {
        "cidade": cidade,
        "uf": uf,
        "regiao": _slug(cidade, uf),
        "rotulo": f"{cidade} - {uf}",
    }
