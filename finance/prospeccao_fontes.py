"""Fontes de captação e enriquecimento da prospecção.

- Google Places API (New) Text Search: puxa comércio real por segmento + cidade
  (nome, endereço, telefone, se tem site, nota e nº de avaliações).
- Filtro de redes grandes (Petz, Drogasil…) pra sobrar só o comércio independente.
- Temperatura automática: comércio ativo SEM site = quente (é o alvo do ZAQ).
- Enriquecimento de CNPJ via BrasilAPI (grátis, sem chave): sócio, regime, porte.

Usa httpx (já no requirements). Tudo é tolerante a falha: sem chave ou erro de
rede devolve {"ok": False, "erro": ...} em vez de estourar exceção.
"""
from __future__ import annotations

import os

import httpx

# Redes grandes / franquias que NÃO são alvo (já têm sistema). Match por substring
# no nome, minúsculo e sem exigir acento perfeito.
REDES_GRANDES = (
    "petz", "cobasi", "petlove", "drogasil", "droga raia", "drogaria araujo",
    "pague menos", "pacheco", "drogaria sao paulo", "ultrafarma", "extrafarma",
    "carrefour", "assai", "atacadao", "makro", "big ", "extra ", "pao de acucar",
    "g barbosa", "carvalho", "americanas", "magazine luiza", "magalu",
    "casas bahia", "ponto frio", "renner", "riachuelo", "c&a", "havan",
    "mcdonald", "burger king", "subway", "bob's", "outback", "starbucks",
    "boticario", "o boticario", "natura", "cacau show", "kopenhagen",
    "banco do brasil", "bradesco", "itau", "santander", "caixa economica",
)


def eh_rede_grande(nome: str) -> bool:
    n = (nome or "").strip().lower()
    return any(r in n for r in REDES_GRANDES)


def temperatura_auto(tem_site) -> str:
    """Regra combinada: sem site = quente; com site = morno; desconhecido = frio."""
    if tem_site is False:
        return "quente"
    if tem_site is True:
        return "morno"
    return "frio"


def tem_chave_places() -> bool:
    return bool(os.environ.get("GOOGLE_PLACES_API_KEY"))


def buscar_places(termo: str, cidade: str, api_key: str | None = None,
                  max_resultados: int = 20) -> dict:
    """Text Search (Places API New). Devolve {"ok", "itens": [...], "erro"}.

    Cada item: empresa, endereco, telefone, place_id, tem_site, rating,
    avaliacoes, rede (bool), temperatura.
    """
    key = api_key or os.environ.get("GOOGLE_PLACES_API_KEY")
    if not key:
        return {"ok": False, "erro": "sem_chave", "itens": []}
    termo = (termo or "").strip()
    cidade = (cidade or "").strip()
    if not termo:
        return {"ok": False, "erro": "sem_termo", "itens": []}
    url = "https://places.googleapis.com/v1/places:searchText"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": key,
        "X-Goog-FieldMask": (
            "places.id,places.displayName,places.formattedAddress,"
            "places.nationalPhoneNumber,places.websiteUri,places.rating,"
            "places.userRatingCount"
        ),
    }
    consulta = f"{termo} em {cidade}" if cidade else termo
    body = {"textQuery": consulta, "languageCode": "pt-BR",
            "regionCode": "BR", "maxResultCount": min(max(max_resultados, 1), 20)}
    try:
        r = httpx.post(url, json=body, headers=headers, timeout=25)
        if r.status_code != 200:
            return {"ok": False, "erro": f"http_{r.status_code}", "itens": [],
                    "detalhe": r.text[:300]}
        data = r.json()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "erro": "rede", "detalhe": str(e)[:200], "itens": []}

    itens = []
    for p in data.get("places", []):
        nome = (p.get("displayName") or {}).get("text") or ""
        if not nome:
            continue
        tem_site = "websiteUri" in p
        itens.append({
            "empresa": nome,
            "endereco": p.get("formattedAddress") or "",
            "telefone": p.get("nationalPhoneNumber") or "",
            "place_id": p.get("id") or "",
            "tem_site": tem_site,
            "rating": p.get("rating"),
            "avaliacoes": p.get("userRatingCount") or 0,
            "rede": eh_rede_grande(nome),
            "temperatura": temperatura_auto(tem_site),
        })
    return {"ok": True, "itens": itens, "erro": None}


def enriquecer_cnpj(cnpj: str) -> dict:
    """Puxa dados públicos do CNPJ na BrasilAPI (grátis, sem chave).

    Devolve {"ok", "dados": {...}} ou {"ok": False, "erro"}. Campos: socio,
    regime_tributario, porte, telefone, email, segmento, cidade, uf, razao_social.
    """
    d = "".join(c for c in (cnpj or "") if c.isdigit())
    if len(d) != 14:
        return {"ok": False, "erro": "cnpj_invalido"}
    try:
        r = httpx.get(f"https://brasilapi.com.br/api/cnpj/v1/{d}", timeout=20)
        if r.status_code != 200:
            return {"ok": False, "erro": f"http_{r.status_code}"}
        j = r.json()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "erro": "rede", "detalhe": str(e)[:200]}

    qsa = j.get("qsa") or []
    socio = None
    if qsa:
        socio = qsa[0].get("nome_socio") or qsa[0].get("nome") or None
    if j.get("opcao_pelo_mei"):
        regime = "MEI"
    elif j.get("opcao_pelo_simples"):
        regime = "Simples Nacional"
    else:
        regime = None
    tel = (j.get("ddd_telefone_1") or "").strip() or None
    dados = {
        "razao_social": j.get("razao_social") or j.get("nome_fantasia"),
        "socio": socio,
        "regime_tributario": regime,
        "porte": (j.get("porte") or "").title().strip() or None,
        "telefone": tel,
        "email": (j.get("email") or "").strip().lower() or None,
        "segmento": j.get("cnae_fiscal_descricao") or None,
        "cidade": (j.get("municipio") or "").title().strip() or None,
        "uf": (j.get("uf") or "").strip().upper() or None,
    }
    return {"ok": True, "dados": dados}
