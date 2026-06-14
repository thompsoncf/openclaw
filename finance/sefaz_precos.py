"""Cliente da API SEFAZ Menor Preco (base aberta Nota Parana - cobre PR e PE).

Consulta precos REAIS de nota fiscal, em tempo real, num raio geografico. E' a
fonte oficial: cada preco vem da ultima NFC-e emitida pelo estabelecimento.

Confirmado por sondagem que o endpoint responde de servidores (ex: Render):
  GET /api/v1/produtos?local=LAT,LON&termo=PRODUTO&raio=KM&offset=0

Cobertura: estados que usam a plataforma Celepar/Nota Parana (PR, PE...).
Fora dela (ex: PI), retorna vazio - ai' o comparador usa o banco proprio.

Esta camada e' TOLERANTE A FALHA: qualquer erro (timeout, bloqueio, formato)
retorna lista vazia, nunca quebra o fluxo do usuario.
"""
from __future__ import annotations

import json
import ssl
import urllib.parse
import urllib.request
from datetime import datetime

_BASE = "https://menorpreco.notaparana.pr.gov.br/api/v1/produtos"
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "pt-BR,pt;q=0.9",
    "Referer": "https://menorpreco.notaparana.pr.gov.br/",
}


class SefazMenorPreco:
    """Consulta a API publica de preco. Stateless e tolerante a falha."""

    def __init__(self, timeout: int = 12):
        self.timeout = timeout

    def disponivel(self, lat: float, lon: float, raio_km: int = 15) -> bool:
        """True se a API retorna dados pra esta regiao (ex: PR/PE)."""
        return len(self.buscar("arroz", lat, lon, raio_km)) > 0

    def buscar(self, termo: str, lat: float, lon: float, raio_km: int = 15,
               limite: int = 30) -> list[dict]:
        """Busca um produto. Retorna lista normalizada de precos por
        estabelecimento (ja' ordenada do mais barato). Vazia em qualquer erro."""
        termo = (termo or "").strip()
        if not termo:
            return []
        params = urllib.parse.urlencode({
            "local": f"{lat},{lon}", "termo": termo, "raio": raio_km, "offset": 0,
        })
        url = f"{_BASE}?{params}"
        try:
            req = urllib.request.Request(url, headers=_HEADERS)
            with urllib.request.urlopen(req, timeout=self.timeout,
                                        context=ssl.create_default_context()) as r:
                dados = json.loads(r.read().decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001
            return []
        produtos = dados.get("produtos") or []
        achados = []
        for p in produtos[:limite]:
            try:
                valor = float(p.get("valor") or p.get("valor_tabela") or 0)
            except (TypeError, ValueError):
                continue
            if valor <= 0:
                continue
            est = p.get("estabelecimento") or {}
            achados.append({
                "descricao": p.get("desc", "").strip(),
                "valor_centavos": int(round(valor * 100)),
                "mercado": (est.get("nm_fan") or est.get("nm_emp") or "").strip() or "(sem nome)",
                "bairro": (est.get("bairro") or "").strip(),
                "cidade": (est.get("mun") or "").strip(),
                "uf": (est.get("uf") or "").strip(),
                "distancia_km": _f(p.get("distkm")),
                "data": _data(p.get("datahora")),
                "gtin": (p.get("gtin") or "").strip() or None,
            })
        achados.sort(key=lambda x: x["valor_centavos"])
        return achados

    def comparar_cesta(self, itens: list[str], lat: float, lon: float,
                       raio_km: int = 15) -> dict:
        """Mesma estrutura do BancoPrecos.comparar_cesta, mas com dados da API."""
        detalhe_itens = []
        mercados: dict[str, dict] = {}
        total_obs = 0
        for desc in itens:
            precos = self.buscar(desc, lat, lon, raio_km)
            por_mercado: dict[str, dict] = {}
            for p in precos:
                por_mercado.setdefault(p["mercado"], p)
            precos = sorted(por_mercado.values(), key=lambda x: x["valor_centavos"])
            total_obs += len(precos)
            if precos:
                melhor = precos[0]
                detalhe_itens.append({
                    "descricao": desc, "melhor_mercado": melhor["mercado"],
                    "melhor_centavos": melhor["valor_centavos"], "precos": precos,
                })
                for p in precos:
                    m = mercados.setdefault(p["mercado"], {"soma": 0, "cobertos": 0, "faltando": []})
                    m["soma"] += p["valor_centavos"]
                    m["cobertos"] += 1
            else:
                detalhe_itens.append({"descricao": desc, "melhor_mercado": None,
                                      "melhor_centavos": None, "precos": []})
        nomes = [d["descricao"] for d in detalhe_itens]
        for nome_merc, m in mercados.items():
            tem = {d["descricao"] for d in detalhe_itens
                   if any(p["mercado"] == nome_merc for p in d["precos"])}
            m["faltando"] = [n for n in nomes if n not in tem]
        ranking = [{"mercado": k, "total_centavos": v["soma"],
                    "itens_cobertos": v["cobertos"], "itens_faltando": v["faltando"]}
                   for k, v in mercados.items()]
        ranking.sort(key=lambda x: (-x["itens_cobertos"], x["total_centavos"]))
        return {"mercados": ranking, "itens": detalhe_itens, "observacoes": total_obs}


def _f(v) -> float | None:
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def _data(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except (ValueError, AttributeError):
        return None
