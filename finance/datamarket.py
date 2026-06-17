"""Cliente da API Data Market (Cnova Tech): preco de mercado por EAN.

Dado COMPLEMENTAR ao banco colaborativo do OpenClaw: o banco interno tem o preco
REAL da loja fisica de Teresina (do cupom); esta API traz preco de e-commerce/
delivery de ~250 redes (coleta diaria). Serve pra cruzar: "voce pagou X, a media
de mercado e' Y" e pra preencher produto que ninguem escaneou ainda.

A chave NUNCA vai no codigo. Lê de DATAMARKET_API_KEY (env do Render).
Endpoint:  GET /api/v1/market/products/ean/{ean}   header X-API-Key
"""
from __future__ import annotations

import os
import logging

_log = logging.getLogger("openclaw.datamarket")
_BASE = "https://datamarket.cnovatech.com.br/api/v1/market"


def disponivel() -> bool:
    """True se a chave estiver configurada (pra ligar/desligar o recurso)."""
    return bool(os.environ.get("DATAMARKET_API_KEY"))


def consultar_preco_ean(ean: str, timeout: float = 8.0) -> dict | None:
    """Consulta o preco de mercado de um produto pelo EAN.

    Retorna um dict NORMALIZADO (centavos, pra casar com o resto do OpenClaw) ou
    None se: sem chave, EAN invalido, nao encontrado, ou falha de rede/HTTP.
    Nunca levanta excecao pro chamador - falha de fonte externa nao quebra o fluxo.
    """
    key = os.environ.get("DATAMARKET_API_KEY")
    if not key:
        _log.warning("DATAMARKET_API_KEY nao configurada - consulta ignorada")
        return None
    ean = "".join(c for c in str(ean or "") if c.isdigit())
    if len(ean) not in (8, 12, 13, 14):
        return None
    import httpx
    try:
        r = httpx.get(f"{_BASE}/products/ean/{ean}",
                      headers={"X-API-Key": key}, timeout=timeout)
        if r.status_code == 404:
            return None                      # produto nao esta' na base
        r.raise_for_status()
        data = r.json()
    except Exception:  # noqa: BLE001
        _log.exception("falha consultando Data Market (ean=%s)", ean)
        return None
    return _normalizar(data)


def _reais_para_centavos(v) -> int | None:
    try:
        return round(float(v) * 100)
    except (TypeError, ValueError):
        return None


def _normalizar(data: dict) -> dict | None:
    """Converte o JSON da API no formato interno (valores em CENTAVOS)."""
    s = data.get("summary") or {}
    if not s:
        return None
    # loja mais barata (menor preco em estoque), se vier a lista
    mais_barata = None
    lojas = data.get("stores") or []
    em_estoque = [x for x in lojas if x.get("in_stock") and x.get("price") is not None]
    if em_estoque:
        b = min(em_estoque, key=lambda x: x["price"])
        mais_barata = {
            "loja": b.get("store"), "uf": b.get("state"),
            "canal": b.get("channel"),
            "preco_centavos": _reais_para_centavos(b.get("price")),
            "atualizado": b.get("updated_date"),
        }
    return {
        "ean": data.get("ean"),
        "produto": data.get("product"),
        "marca": data.get("brand"),
        "categoria": data.get("category"),
        "departamento": data.get("department"),
        "menor_centavos": _reais_para_centavos(s.get("lowest_price")),
        "tipico_centavos": _reais_para_centavos(s.get("typical_price")),
        "media_centavos": _reais_para_centavos(s.get("average_price")),
        "maior_centavos": _reais_para_centavos(s.get("highest_price")),
        "lojas": s.get("stores"),
        "regioes": s.get("regions"),
        "variacao_pct": s.get("price_variation_pct"),
        "disponibilidade_pct": s.get("availability"),
        "mais_barata": mais_barata,
    }
