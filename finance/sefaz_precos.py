"""Cliente da API SEFAZ Menor Preco (base aberta Nota Parana - cobre PR e PE).

Consulta precos REAIS de nota fiscal, em tempo real, num raio geografico.
Casamento forte (palavra do termo na descricao) + descarte de outliers baratos.
Classifica estabelecimento (mercado/farmacia) e traz endereco/distancia.
Tolerante a falha: qualquer erro retorna vazio, nunca quebra o fluxo.
"""
from __future__ import annotations

import json
import ssl
import unicodedata
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


def _norm(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", (t or "").lower())
                   if unicodedata.category(c) != "Mn")


class SefazMenorPreco:
    """Consulta a API publica de preco. Stateless e tolerante a falha."""

    def __init__(self, timeout: int = 12):
        self.timeout = timeout

    def disponivel(self, lat: float, lon: float, raio_km: int = 15) -> bool:
        return len(self.buscar("arroz", lat, lon, raio_km)) > 0

    def opcoes_produto(self, termo: str, lat: float, lon: float, raio_km: int = 15,
                       limite: int = 8) -> list[dict]:
        """Variantes DISTINTAS de produto que casam com o termo, pro cliente
        escolher no 'ajustar'. Agrupa por descricao normalizada, mostra a faixa
        de preco de cada variante. Ordenado por mais barato."""
        precos = self.buscar(termo, lat, lon, raio_km, limite=40)
        grupos: dict[str, dict] = {}
        for p in precos:
            chave = " ".join(_norm(p["descricao"]).split()[:3])
            g = grupos.setdefault(chave, {"descricao": p["descricao"],
                                          "min": p["valor_centavos"], "max": p["valor_centavos"],
                                          "n": 0})
            g["min"] = min(g["min"], p["valor_centavos"])
            g["max"] = max(g["max"], p["valor_centavos"])
            g["n"] += 1
        opcoes = sorted(grupos.values(), key=lambda x: x["min"])
        return opcoes[:limite]

    def buscar(self, termo: str, lat: float, lon: float, raio_km: int = 15,
               limite: int = 30) -> list[dict]:
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
        # palavras significativas do termo (pra casar so' o produto certo)
        alvo = [w for w in _norm(termo).split() if len(w) > 2]
        achados = []
        for p in produtos[:limite]:
            try:
                valor = float(p.get("valor") or p.get("valor_tabela") or 0)
            except (TypeError, ValueError):
                continue
            if valor <= 0:
                continue
            desc = p.get("desc", "").strip()
            # CASAMENTO FORTE: a palavra do termo precisa aparecer como PALAVRA
            # INTEIRA na descricao (nao substring), e idealmente entre as 2
            # primeiras palavras (o nucleo do produto). Evita 'acucar' casar com
            # 'bala acucar' / 'acucar de confeiteiro p/ bolo'.
            palavras_desc = _norm(desc).split()
            nucleo = set(palavras_desc[:2])           # 2 primeiras = o produto
            if alvo and not any(w in nucleo for w in alvo):
                continue
            est = p.get("estabelecimento") or {}
            nome_merc = (est.get("nm_fan") or est.get("nm_emp") or "").strip() or "(sem nome)"
            logr = " ".join(x for x in [
                (est.get("tp_logr") or "").strip(), (est.get("nm_logr") or "").strip(),
                (est.get("nr_logr") or "").strip()] if x).strip()
            achados.append({
                "descricao": desc,
                "valor_centavos": int(round(valor * 100)),
                "mercado": nome_merc,
                "tipo": _tipo_estabelecimento(nome_merc, p.get("ncm")),
                "endereco": logr,
                "bairro": (est.get("bairro") or "").strip(),
                "cidade": (est.get("mun") or "").strip(),
                "uf": (est.get("uf") or "").strip(),
                "distancia_km": _f(p.get("distkm")),
                "data": _data(p.get("datahora")),
                "gtin": (p.get("gtin") or "").strip() or None,
            })
        achados = _sem_outliers(achados)
        achados.sort(key=lambda x: x["valor_centavos"])
        return achados

    def comparar_cesta(self, itens: list[str], lat: float, lon: float,
                       raio_km: int = 15) -> dict:
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
                    m = mercados.setdefault(p["mercado"], {
                        "soma": 0, "cobertos": 0, "faltando": [], "produtos": [],
                        "tipo": p.get("tipo", "outro"), "bairro": p.get("bairro", ""),
                        "distancia_km": p.get("distancia_km")})
                    m["soma"] += p["valor_centavos"]
                    m["cobertos"] += 1
                    m["produtos"].append({"descricao": p["descricao"],
                                          "valor_centavos": p["valor_centavos"]})
            else:
                detalhe_itens.append({"descricao": desc, "melhor_mercado": None,
                                      "melhor_centavos": None, "precos": []})
        nomes = [d["descricao"] for d in detalhe_itens]
        for nome_merc, m in mercados.items():
            tem = {d["descricao"] for d in detalhe_itens
                   if any(p["mercado"] == nome_merc for p in d["precos"])}
            m["faltando"] = [n for n in nomes if n not in tem]
        ranking = [{"mercado": k, "total_centavos": v["soma"],
                    "itens_cobertos": v["cobertos"], "itens_faltando": v["faltando"],
                    "tipo": v["tipo"], "bairro": v["bairro"], "distancia_km": v["distancia_km"],
                    "produtos": v["produtos"]}
                   for k, v in mercados.items()]
        ranking.sort(key=lambda x: (-x["itens_cobertos"], x["total_centavos"]))
        return {"mercados": ranking, "itens": detalhe_itens, "observacoes": total_obs}


def _tipo_estabelecimento(nome: str, ncm=None) -> str:
    """Classifica em 'farmacia', 'mercado' ou 'outro' pelo nome (e NCM se util)."""
    n = _norm(nome)
    farmacia = ("farmacia", "drogaria", "drogasil", "pacheco", "raia", "panvel",
                "pague menos", "nissei", "permanente", "ultrafarma",
                "extrafarma", "venancio", "popular")
    if any(t in n for t in farmacia):
        return "farmacia"
    if ncm and str(ncm).startswith("30"):
        return "farmacia"
    mercado = ("super", "mercado", "atacad", "atacarejo", "hiper", "armazem",
               "mercearia", "comercial", "alimentos", "assai", "carrefour",
               "big", "makro", "sams", "frangos", "hortifruti", "sacolao")
    if any(t in n for t in mercado):
        return "mercado"
    return "outro"


def _sem_outliers(achados: list[dict]) -> list[dict]:
    """Descarta precos absurdamente baixos (item errado: sache, bala, unidade
    avulsa). Duas defesas:
    1. PISO ABSOLUTO: remove tudo abaixo de R$ 0,50 (50 centavos).
    2. MEDIANA: com >= 3 precos, remove o que estiver abaixo de 35% da mediana.
    Se as defesas zerarem tudo, devolve o original (nao esconde dado legitimo)."""
    if not achados:
        return achados
    filtrados = [a for a in achados if a["valor_centavos"] >= 50]
    base = filtrados or achados
    if len(base) >= 3:
        vals = sorted(a["valor_centavos"] for a in base)
        meio = len(vals) // 2
        mediana = vals[meio] if len(vals) % 2 else (vals[meio - 1] + vals[meio]) / 2
        piso = mediana * 0.35
        base2 = [a for a in base if a["valor_centavos"] >= piso]
        base = base2 or base
    return base


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
