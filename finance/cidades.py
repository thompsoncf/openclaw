"""Cidades cobertas pelo comparador de precos.

Cada cidade tem coordenada do centro (pra busca SEFAZ por raio) e a fonte de
preco esperada: 'sefaz' (API aberta cobre - PR/PE) ou 'proprio' (so' banco
proprio dos cupons - ex: Teresina). O comparador tenta a SEFAZ e, se vier
vazio, cai no banco proprio - entao 'fonte' e' so' uma dica/ordem de tentativa.

Comecar pequeno e CURADO: so' cidades onde a gente tem dado de verdade.
"""
from __future__ import annotations

CIDADES: dict[str, tuple[str, float, float, int, str]] = {
    "teresina-pi":  ("Teresina - PI", -5.0892, -42.8016, 20, "proprio"),
    "curitiba-pr":  ("Curitiba - PR", -25.4284, -49.2733, 15, "sefaz"),
    "recife-pe":    ("Recife - PE", -8.0476, -34.8770, 15, "sefaz"),
    "londrina-pr":  ("Londrina - PR", -23.3045, -51.1696, 15, "sefaz"),
    "maringa-pr":   ("Maringá - PR", -23.4253, -51.9386, 15, "sefaz"),
}

_PADRAO = "teresina-pi"


def opcoes() -> list[tuple[str, str]]:
    return [(cod, dados[0]) for cod, dados in CIDADES.items()]


def coordenada(cidade: str | None) -> tuple[float, float, int] | None:
    d = CIDADES.get((cidade or "").strip().lower())
    return (d[1], d[2], d[3]) if d else None


def fonte(cidade: str | None) -> str:
    d = CIDADES.get((cidade or "").strip().lower())
    return d[4] if d else "proprio"


def rotulo(cidade: str | None) -> str:
    d = CIDADES.get((cidade or "").strip().lower())
    return d[0] if d else (cidade or "—")


def valida(cidade: str | None) -> str:
    c = (cidade or "").strip().lower()
    return c if c in CIDADES else _PADRAO
