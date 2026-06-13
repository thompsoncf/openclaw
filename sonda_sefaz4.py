"""Sonda v4 - servidor REAL da SEFAZ-PI (webas.sefaz.pi.gov.br) descoberto.

Procura: API do Menor Preco hospedada no PI, e/ou consulta publica de NFC-e.
Roda no Render:  python sonda_sefaz4.py   -> cola a saida no chat.
"""
import json, urllib.request, urllib.error, ssl

_H = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "pt-BR,pt;q=0.9",
}

ALVOS = [
    ("webas raiz", "https://webas.sefaz.pi.gov.br/"),
    ("webas menorpreco", "https://webas.sefaz.pi.gov.br/menorpreco/"),
    ("webas mp api", "https://webas.sefaz.pi.gov.br/menorpreco/api/v1/produtos?local=-5.0892,-42.8016&termo=arroz&raio=15"),
    ("webas mpb", "https://webas.sefaz.pi.gov.br/mpb/api/v1/produtos?local=-5.0892,-42.8016&termo=arroz&raio=15"),
    ("webas precos", "https://webas.sefaz.pi.gov.br/precos/api/v1/produtos?local=-5.0892,-42.8016&termo=arroz&raio=15"),
    ("webas notapiauiense", "https://webas.sefaz.pi.gov.br/notapiauiense/"),
    ("portal sefaz mp", "https://portal.sefaz.pi.gov.br/menorprecobrasil/"),
    ("portal sefaz mpb api", "https://portal.sefaz.pi.gov.br/menorprecobrasil/api/v1/produtos?local=-5.0892,-42.8016&termo=arroz&raio=15"),
    ("siat web", "https://webas.sefaz.pi.gov.br/siatweb/"),
]

def testar(rotulo, url):
    print("\n" + "="*70)
    print(f"[{rotulo}]  GET {url}")
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=_H), timeout=20,
                                    context=ssl.create_default_context()) as r:
            body = r.read(2500).decode("utf-8","replace")
            ctype = r.headers.get("Content-Type","")
            print(f"STATUS {r.status} | {ctype}")
            if body.lstrip().startswith(("{","[")):
                try:
                    d = json.loads(body)
                    total = d.get("total") if isinstance(d, dict) else len(d)
                    print(f">>> JSON! total={total}")
                    print(json.dumps(d, indent=2, ensure_ascii=False)[:1500])
                    return (f"JSON total={total}", rotulo)
                except Exception:
                    print("(JSON-ish)", body[:500])
            else:
                low = body.lower()
                import re
                titulo = re.search(r"<title>(.*?)</title>", body, re.I|re.S)
                t = titulo.group(1).strip()[:80] if titulo else ""
                sinal = "gov.br" if "gov.br" in low or "govbr" in low else ("menor pre" if "menor pre" in low else "")
                print(f"(HTML) titulo='{t}' sinal='{sinal}'")
                print(body[:200].replace(chr(10)," "))
            return (f"{r.status}", rotulo)
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code} {e.reason}")
        return (f"HTTP {e.code}", rotulo)
    except Exception as e:
        print("FALHOU:", type(e).__name__, str(e)[:150])
        return ("ERRO", rotulo)

print("SONDA v4 - servidor real SEFAZ-PI (webas) + Menor Preco PI")
res = [testar(r,u) for r,u in ALVOS]
print("\n"+"#"*70+"\nRESUMO:")
for s,r in res: print(f"  [{s}] {r}")
print("\n[JSON total>0] = achamos a base do PI!  Veja titulos HTML p/ pistas de caminho.")
