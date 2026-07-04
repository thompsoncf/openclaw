"""Reverse geocoding: lat/lng -> endereco (rua/bairro/cidade/uf/cep).

Fonte atual: Nominatim (OpenStreetMap). Gratis, sem chave, sem cartao.
Serve pra opcao "usar minha localizacao" no checkout da loja: o navegador
pega a lat/lng do aparelho (com permissao) e aqui a gente traduz em endereco
pra PRE-PREENCHER os campos. A pessoa confirma e completa o numero.

PROVEDOR ISOLADO DE PROPOSITO: trocar por LocationIQ / OpenCage / MapTiler /
Google e' mudar SO ESTE ARQUIVO, mantendo a assinatura reverse() e o formato
de retorno. (Todos "sabor OSM" tem resposta parecida; Google muda o parse.)

VERDADES sobre reverse geocoding no Brasil (nao prometer demais):
  - Numero da casa o GPS nunca traz - a pessoa sempre digita.
  - CEP (postcode) as vezes vem VAZIO no Nominatim - tudo bem, o endereco de
    entrega funciona sem CEP e a regiao a gente tira da cidade.
  - Precisao do GPS em bairro denso ~10-50m - pode cair na rua vizinha.
    Por isso rua/bairro/cep vem como sugestao EDITAVEL, nunca travados.
Logo: rua/bairro/cep podem ser '' ; cidade e' o minimo util (sem cidade -> None).

POLITICA Nominatim: ~1 req/s no maximo e User-Agent identificavel obrigatorio.
A gente NAO guarda a lat/lng (so usa na hora pra montar o endereco em texto),
entao fica dentro do ToS. No Render a rede e' liberada; no sandbox local o
Nominatim e' bloqueado, entao o parse e' testado com mock via _montar().

Trocar de provedor depois: reescrever reverse() (a chamada http) e, se preciso,
_montar() (o parse). Quem chama nao muda.
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
import urllib.parse
import urllib.request

_log = logging.getLogger("openclaw.geo")
_TIMEOUT = 6
_NOMINATIM = "https://nominatim.openstreetmap.org/reverse"
# User-Agent identificavel e' exigido pela politica do Nominatim.
_UA = "ZAQ/1.0 (+https://app.zaq-ia.com; contato@zaq-ia.com)"

# Fallback nome-do-estado -> sigla (sem acento, minusculo). So usado quando
# o Nominatim nao traz o ISO3166-2 (ex "BR-PI"), o que e' raro.
_UF_NOME = {
    "acre": "AC", "alagoas": "AL", "amapa": "AP", "amazonas": "AM",
    "bahia": "BA", "ceara": "CE", "distrito federal": "DF",
    "espirito santo": "ES", "goias": "GO", "maranhao": "MA",
    "mato grosso": "MT", "mato grosso do sul": "MS", "minas gerais": "MG",
    "para": "PA", "paraiba": "PB", "parana": "PR", "pernambuco": "PE",
    "piaui": "PI", "rio de janeiro": "RJ", "rio grande do norte": "RN",
    "rio grande do sul": "RS", "rondonia": "RO", "roraima": "RR",
    "santa catarina": "SC", "sao paulo": "SP", "sergipe": "SE",
    "tocantins": "TO",
}


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _sem_acento(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return s.strip().lower()


def _slug(cidade: str, uf: str) -> str:
    """Chave de regiao no padrao do banco: 'teresina-pi' (sem acento)."""
    base = f"{cidade}-{uf}".strip("-").lower() if uf else (cidade or "").lower()
    base = unicodedata.normalize("NFKD", base).encode("ascii", "ignore").decode()
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    return base


def _uf_de(address: dict) -> str:
    """Extrai a sigla da UF: prefere ISO3166-2 (BR-PI), cai pro mapa de nomes."""
    iso = (address.get("ISO3166-2-lvl4") or "").strip().upper()
    if iso.startswith("BR-") and len(iso) == 5:
        return iso[3:]
    return _UF_NOME.get(_sem_acento(address.get("state") or ""), "")


def _montar(dados: dict) -> dict | None:
    """Normaliza a resposta crua do Nominatim num dict estavel (ou None).

    Separado de reverse() so pra ser testavel sem rede.
    """
    address = dados.get("address") or {}
    cidade = (
        address.get("city") or address.get("town") or address.get("municipality")
        or address.get("village") or address.get("county") or ""
    ).strip()
    if not cidade:
        return None
    uf = _uf_de(address)
    rua = (
        address.get("road") or address.get("pedestrian")
        or address.get("residential") or address.get("footway") or ""
    ).strip()
    bairro = (
        address.get("suburb") or address.get("neighbourhood")
        or address.get("quarter") or address.get("city_district") or ""
    ).strip()
    cep = re.sub(r"\D", "", address.get("postcode") or "")
    return {
        "cidade": cidade,
        "uf": uf,
        "rua": rua,
        "bairro": bairro,
        "cep": cep,  # pode ser '' ; formatado com hifen so na UI
        "regiao": _slug(cidade, uf),
        "rotulo": f"{cidade} - {uf}" if uf else cidade,
    }


def reverse(lat, lng) -> dict | None:
    """lat/lng -> dict {cidade,uf,rua,bairro,cep,regiao,rotulo} ou None.

    rua/bairro/cep podem vir '' (o Nominatim nem sempre traz no BR); cidade e'
    o minimo. Qualquer erro de rede/parse -> None (o form segue no manual).
    """
    la, lo = _num(lat), _num(lng)
    if la is None or lo is None:
        return None
    if not (-90.0 <= la <= 90.0 and -180.0 <= lo <= 180.0):
        return None
    params = urllib.parse.urlencode({
        "lat": la,
        "lon": lo,
        "format": "jsonv2",
        "addressdetails": 1,
        "accept-language": "pt-BR",
        "zoom": 18,  # nivel de rua
    })
    try:
        req = urllib.request.Request(
            f"{_NOMINATIM}?{params}",
            headers={"User-Agent": _UA},
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            dados = json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001 - nunca trava o checkout
        _log.info("geo %s,%s falhou: %s: %s", lat, lng, type(e).__name__, e)
        return None

    if not isinstance(dados, dict) or dados.get("error"):
        return None
    return _montar(dados)
