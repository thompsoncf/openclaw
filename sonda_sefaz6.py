#!/usr/bin/env python3
"""Sonda v6 - ver o que a SEFAZ retorna p/ cada item da lista de teste.
Entender por que so' 'arroz' casa e os outros somem. Roda no Render."""
import json
import urllib.request
import ssl

LAT, LON, RAIO = -25.4284, -49.2733, 15
_H = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json"
}

def ver(termo):
    url = f"https://menorpreco.notaparana.pr.gov.br/api/v1/produtos?local={LAT},{LON}&termo={termo}&raio={RAIO}&offset=0"
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, headers=_H),
            timeout=15,
            context=ssl.create_default_context()
        ) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        print(f"[{termo}] ERRO {e}")
        return

    prods = d.get("produtos", [])
    print(f"\n=== '{termo}': total={d.get('total')} ===")
    vals = sorted(prods, key=lambda p: float(p.get("valor") or 9999))
    for p in vals[:8]:
        desc = p.get("desc", "")
        ncm = p.get("ncm", "")
        valor = p.get("valor", "?")
        print(f"    R$ {valor:>8} | NCM {ncm:>10} | {desc[:45]}")

if __name__ == "__main__":
    for t in ["pao de alho", "picanha", "arroz", "cafe", "acucar", "leite"]:
        ver(t)
    print("\n>>> Veja a 1a palavra de cada: e' o produto certo ou um derivado?")
