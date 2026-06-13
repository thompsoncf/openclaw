"""Sonda v3 - base NACIONAL (Procergs/RS) que cobre Teresina-PI.

Descobre o dominio real e O QUE exige (token leve vs gov.br completo).
Roda no Render:  python sonda_sefaz3.py   -> cola a saida no chat.
"""
import json, urllib.request, urllib.error, ssl

_H = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "pt-BR,pt;q=0.9",
}

LAT, LON = -5.0892, -42.8016   # Teresina-PI

ALVOS = [
    ("menorpreco.sefazrs", "https://menorpreco.sefaz.rs.gov.br/"),
    ("precojusto.sefazrs", "https://precojusto.sefaz.rs.gov.br/"),
    ("mpb api produtos", "https://menorpreco.sefaz.rs.gov.br/api/v1/produtos?local=-5.0892,-42.8016&termo=arroz&raio=15&offset=0"),
    ("menorprecobrasil.com", "https://menorprecobrasil.com.br/"),
    ("mpbr procergs", "https://mpbr.procergs.rs.gov.br/"),
    ("menorpreco app rs", "https://menorpreco.apprs.com.br/"),
    ("nfg menorpreco", "https://nfg.sefaz.rs.gov.br/site/menorpreco.aspx"),
    ("dfe-portal rs", "https://dfe-portal.svrs.rs.gov.br/Mpb"),
    ("mpb svrs api", "https://dfe-portal.svrs.rs.gov.br/Mpb/api/v1/produtos?local=-5.0892,-42.8016&termo=arroz&raio=15"),
]

def testar(rotulo, url):
    print("\n" + "="*70)
    print(f"[{rotulo}]  GET {url}")
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=_H), timeout=20,
                                    context=ssl.create_default_context()) as r:
            body = r.read(2500).decode("utf-8","replace")
            ctype = r.headers.get("Content-Type","")
            www_auth = r.headers.get("WWW-Authenticate","")
            print(f"STATUS {r.status} | {ctype}" + (f" | auth: {www_auth}" if www_auth else ""))
            if body.lstrip().startswith(("{","[")):
                try:
                    d = json.loads(body)
                    total = d.get("total") if isinstance(d, dict) else len(d)
                    print(f">>> JSON! total={total}")
                    print(json.dumps(d, indent=2, ensure_ascii=False)[:1500])
                    return (f"JSON total={total}", rotulo)
                except Exception:
                    print(body[:600])
            else:
                low = body.lower()
                sinal = "gov.br" if "gov.br" in low or "govbr" in low else ("login" if "login" in low else "html")
                print(f"(HTML - sinal: {sinal})", body[:200].replace(chr(10)," "))
            return (f"{r.status}/{ctype[:20]}", rotulo)
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code} {e.reason} | auth: {e.headers.get('WWW-Authenticate','')}")
        try: print("corpo:", e.read(300).decode("utf-8","replace")[:300])
        except Exception: pass
        return (f"HTTP {e.code}", rotulo)
    except Exception as e:
        print("FALHOU:", type(e).__name__, str(e)[:150])
        return ("ERRO", rotulo)

print("SONDA v3 - base nacional Procergs/RS (cobre Teresina)")
res = [testar(r,u) for r,u in ALVOS]
print("\n"+"#"*70+"\nRESUMO:")
for s,r in res: print(f"  [{s}] {r}")
print("\n[JSON total>0] = achamos e e' aberta!  [HTTP 401/403] = exige login.")
print("Repare em 'auth:' e no sinal 'gov.br' - dizem SE e COMO exige autenticacao.")
