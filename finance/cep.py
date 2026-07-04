"""Descobre cidade/UF/rua/bairro a partir do CEP, via BrasilAPI.

Usado no cadastro pra NAO obrigar a pessoa a escolher cidade numa lista
(ela digita o CEP; aqui a gente descobre cidade/estado e monta a chave de
regiao que o banco de precos usa, ex: "teresina-pi") e tambem no checkout
da loja pra AUTOPREENCHER rua e bairro (a pessoa so completa numero e
complemento).

BrasilAPI v2 ja' devolve street/neighborhood (rua/bairro) alem de city/state;
antes a gente so aproveitava city/state. Agora expomos rua/bairro tambem.
Campos rua/bairro podem vir VAZIOS (CEP de cidade pequena ou CEP unico de
bairro grande cobre area ampla) - nesse caso o formulario deixa a pessoa
preencher a mao. Por isso rua/bairro sao sempre string (nunca None).

Tolerante a falha: se o CEP for invalido, a API cair, ou a rede falhar, retorna
None - o cadastro/checkout segue sem travar (cidade fica vazia e pode ser
ajustada depois).

BrasilAPI ja' esta' liberada no Render. (No sandbox de teste local ela e'
bloqueada, entao a logica de parse e' testada com mock via _montar().)
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


def _montar(dados: dict) -> dict | None:
    """Normaliza a resposta crua da BrasilAPI num dict estavel (ou None).

    Separado de consultar() so pra ser testavel sem rede.
    """
    cidade = (dados.get("city") or "").strip()
    uf = (dados.get("state") or "").strip().upper()
    if not cidade or not uf:
        return None
    return {
        "cidade": cidade,
        "uf": uf,
        "rua": (dados.get("street") or "").strip(),
        "bairro": (dados.get("neighborhood") or "").strip(),
        "regiao": _slug(cidade, uf),
        "rotulo": f"{cidade} - {uf}",
    }


def consultar(cep: str | None) -> dict | None:
    """Consulta o CEP e devolve dict normalizado ou None.

    Retorno:
      {
        'cidade': 'Teresina',
        'uf': 'PI',
        'rua': 'Rua Sao Pedro',   # pode ser '' (CEP amplo)
        'bairro': 'Centro',        # pode ser '' (CEP amplo)
        'regiao': 'teresina-pi',   # chave normalizada pro banco de precos
        'rotulo': 'Teresina - PI', # nome amigavel
      }
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
    except Exception as e:  # noqa: BLE001 - nunca trava o cadastro/checkout
        _log.info("cep %s falhou: %s: %s", digs, type(e).__name__, e)
        return None

    return _montar(dados)
