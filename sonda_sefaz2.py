"""Sonda v2 - descobre SE a base do PR tem dados e ONDE estao os dados do PIAUI.

Roda no Render:  python sonda_sefaz2.py
Cola TODA a saida no chat.
"""
import json, urllib.request, urllib.error, ssl

_H = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "pt-BR,pt;q=0.9",
    "Referer": "https://menorpreco.notaparana.pr.gov.br/",
    "X-Requested-With": "XMLHttpRequest",
}

ALVOS = [
    ("PR/Curitiba arroz", "https://menorpreco.notaparana.pr.gov.br/api/v1/produtos?local=-25.4284,-49.2733&termo=arroz&raio=15&offset=0"),
    ("PR/Curitiba leite", "https://menorpreco.notaparana.pr.gov.br/api/v1/produtos?local=-25.4284,-49.2733&termo=leite&raio=15&offset=0"),
    ("PI/Teresina raio50 leite", "https://menorpreco.notaparana.pr.gov.br/api/v1/produtos?local=-5.0892,-42.8016&termo=leite&raio=50&offset=0"),
    ("PI dominio menorpreco.pi", "https://menorpreco.sefaz.pi.gov.br/api/v1/produtos?local=-5.0892,-42.8016&termo=arroz&raio=15&offset=0"),
    ("PI dominio webas.sefaz.pi", "https://webas.sefaz.pi.gov.br/menorpreco/api/v1/produtos?local=-5.0892,-42.8016&termo=arroz&raio=15"),
    ("PI portal sefaz", "https://portal.sefaz.pi.gov.br/menorpreco/api/v1/produtos?local=-5.0892,-42.8016&termo=arroz&raio=15"),
    ("BR menorpreco.com.br", "https://menorpreco.com.br/api/v1/produtos?local=-5.0892,-42.8016&termo=arroz&raio=15"),
    ("RS nfg menorpreco", "https://menorpreco.sefaz.rs.gov.br/api/v1/produtos?local=-30.0346,-51.2177&termo=arroz&raio=15"),
]

def testar(rotulo, url):
    print("\n" + "="*70)
    print(f"[{rotulo}]  GET {url}")
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=_H), timeout=20,
                                    context=ssl.create_default_context()) as r:
            body = r.read(3000).decode("utf-8","replace")
            ctype = r.headers.get("Content-Type","")
            print(f"STATUS {r.status} | {ctype}")
            if body.lstrip().startswith(("{","[")):
                try:
                    d = json.loads(body)
                    total = d.get("total") if isinstance(d, dict) else len(d)
                    print(f">>> total de produtos: {total}")
                    print(json.dumps(d, indent=2, ensure_ascii=False)[:2000])
                    return (f"JSON total={total}", rotulo)
                except Exception as e:
                    print("nao parseou:", e); print(body[:800])
            else:
                print("(HTML/nao-JSON)", body[:200])
            return (f"{r.status}", rotulo)
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code} {e.reason}")
        return (f"HTTP {e.code}", rotulo)
    except Exception as e:
        print("FALHOU:", type(e).__name__, str(e)[:150])
        return ("ERRO", rotulo)

print("SONDA v2 - PR (controle) + busca base do PIAUI")
res = [testar(r,u) for r,u in ALVOS]
print("\n"+"#"*70+"\nRESUMO:")
for s,r in res: print(f"  [{s}] {r}")
print("\nSe PR/Curitiba der total>0 e Teresina=0 -> a base e' so' do PR; achar a do PI.")
print("Se algum dominio PI/BR responder JSON com total>0 -> achamos a base do Piaui!")
