#!/usr/bin/env python3
"""Sonda de FILIAIS - VipCommerce / Carvalho Supershop.

Objetivo: descobrir QUAIS filiais/centros de distribuição existem (com nome e id)
e PROVAR que o preço muda por filial. Usa as credenciais capturadas no cURL do
navegador (Bearer de convidado + domainkey + sessão-id). Roda NO RENDER.

  python sonda_filiais.py   -> cola TODA a saída no chat.

Se der 401 em tudo: o token de convidado expirou. Recapture um cURL novo da busca
no navegador e troque AUTH/SESSAO_ID/SESSION abaixo.

Só lê. Stdlib pura.
"""
import json
import re
import ssl
import urllib.request
import urllib.error

# ---- credenciais capturadas (do cURL do navegador) ------------------------
SERVICES = "https://services.vipcommerce.com.br/api-admin/v1"
ORG = "23"
AUTH = ("Bearer eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9."
        "eyJpc3MiOiJ2aXBjb21tZXJjZSIsImF1ZCI6ImFwaS1hZG1pbiIsInN1YiI6IjZiYzQ4"
        "ODY3ZS1kY2E5LTExZTktODc0Mi0wMjBkNzkzNTljYTAiLCJ2aXBjb21tZXJjZUNsaWVu"
        "dGVJZCI6bnVsbCwiaWF0IjoxNzgyNjEzNjQzLCJ2ZXIiOjEsImNsaWVudCI6bnVsbCwi"
        "b3BlcmF0b3IiOm51bGwsIm9yZyI6IjIzIn0."
        "Z9g-iSiCX20vKyBITiDUEhhjDWxiQTkJE8zopvhVc3zC2pUwaMZ9tACLD4tI3s_ow1LX"
        "tp467HUxi002_qGzog")
DOMAINKEY = "carvalhosupershop.com.br"
SESSAO_ID = "60be7ce826dc93b0ff32c8e43fb585d8"   # header sessão-id
SESSION = "a2f4128f-1700-4b42-8916-86620e24773d"  # query ?session=

_H = {
    "accept": "application/json",
    "accept-language": "pt-BR,pt;q=0.9",
    "authorization": AUTH,
    "content-type": "application/json",
    "domainkey": DOMAINKEY,
    "organizationid": ORG,
    "origin": "https://www.carvalhosupershop.com.br",
    "referer": "https://www.carvalhosupershop.com.br/",
    "sessao-id": SESSAO_ID,
    "user-agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"),
}
_CTX = ssl.create_default_context()


def get(url, timeout=20):
    sep = "&" if "?" in url else "?"
    url = f"{url}{sep}session={SESSION}"
    try:
        req = urllib.request.Request(url, headers=_H)
        with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
            return r.status, r.read(800_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            body = e.read(600).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            body = ""
        return e.code, body
    except Exception as e:  # noqa: BLE001
        return None, f"ERRO: {e}"


def secao(t):
    print("\n" + "=" * 70 + f"\n{t}\n" + "=" * 70)


# ----------------------------------------- PARTE A: achar listagem de filiais
secao("PARTE A - tenta listar filiais / centros de distribuição")
CANDIDATOS = [
    f"{SERVICES}/org/{ORG}/filiais",
    f"{SERVICES}/org/{ORG}/loja/filiais",
    f"{SERVICES}/org/{ORG}/centros_distribuicao",
    f"{SERVICES}/org/{ORG}/loja/centros_distribuicao",
    f"{SERVICES}/org/{ORG}/filial/1/centro_distribuicao/1/loja/filiais",
    f"{SERVICES}/org/{ORG}/filial/1/centro_distribuicao/1/loja/centros_distribuicao",
    f"{SERVICES}/org/{ORG}/filial/1/centro_distribuicao/1/loja/lojas",
    f"{SERVICES}/org/{ORG}/loja/lojas",
    f"{SERVICES}/org/{ORG}/loja/enderecos",
    f"{SERVICES}/org/{ORG}/loja",
]
achou_filiais = None
for url in CANDIDATOS:
    st, body = get(url)
    marca = ""
    if st and st < 400 and body[:1].strip() in "[{":
        if re.search(r'"(filial|centro_distribuicao|loja|cidade|bairro|nome)"', body):
            marca = "  <<< PARECE LISTA DE FILIAIS"
            if achou_filiais is None:
                achou_filiais = body
    print(f"  [{st}] {url.replace(SERVICES,'')}{marca}")
    if marca:
        print("       amostra:", body[:500].replace("\n", " "))

# extrai ids de filial/cd da amostra achada (vários formatos possíveis)
pares = set()
if achou_filiais:
    try:
        dados = json.loads(achou_filiais)
        blob = dados.get("data", dados)
        itens = blob if isinstance(blob, list) else (blob.get("filiais") or blob.get("centros_distribuicao") or [])
        for it in itens if isinstance(itens, list) else []:
            fid = it.get("filial_id") or it.get("filial") or it.get("id")
            cd = it.get("centro_distribuicao_id") or it.get("centro_distribuicao") or it.get("cd_id") or 1
            nome = it.get("nome") or it.get("descricao") or it.get("cidade") or "?"
            if fid is not None:
                pares.add((str(fid), str(cd)))
                print(f"   -> filial={fid} cd={cd} nome={nome}")
    except Exception as e:  # noqa: BLE001
        print("   (não consegui parsear automaticamente:", e, ")")


# ------------------------------------------ PARTE B: confirma pelos bundles JS
secao("PARTE B - varre o JS atrás das rotas reais (filial/centro_distribuição)")
WEB = "https://www.carvalhosupershop.com.br"
st, html = get(WEB + "/") if False else (None, "")  # não precisa de session no web
try:
    req = urllib.request.Request(WEB + "/", headers={"user-agent": _H["user-agent"]})
    with urllib.request.urlopen(req, timeout=20, context=_CTX) as r:
        html = r.read().decode("utf-8", "replace")
except Exception as e:  # noqa: BLE001
    html = ""
    print("  (falha ao pegar html:", e, ")")

bundles = re.findall(r'src=["\']([^"\']+\.js)["\']', html)
bundles = [b if b.startswith("http") else WEB + "/" + b.lstrip("/") for b in bundles]
KW = re.compile(r'(filiais|centros_distribuicao|centro_distribuicao|/loja/[a-z_]+|sessao|/auth|token)', re.I)
vistos = set()
for b in bundles:
    if "vlibras" in b or "polyfill" in b:
        continue
    try:
        req = urllib.request.Request(b, headers={"user-agent": _H["user-agent"],
                                                 "accept-encoding": "identity"})
        with urllib.request.urlopen(req, timeout=20, context=_CTX) as r:
            js = r.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        continue
    for m in KW.finditer(js):
        ctx = js[max(0, m.start() - 30):m.end() + 30].replace("\n", " ")
        ctx = re.sub(r"\s+", " ", ctx)
        if ctx not in vistos:
            vistos.add(ctx)
            print("  ...", ctx)
    if len(vistos) > 60:
        break


# -------------------------------- PARTE C: PROVA preço mudando por filial
secao("PARTE C - mesma busca 'arroz' em filiais diferentes (preço muda?)")
# usa os pares achados; se não achou, tenta uma grade pequena
tentativas = sorted(pares) if pares else [("1", "1"), ("1", "2"), ("2", "1"),
                                          ("2", "2"), ("3", "1"), ("4", "1")]
for fid, cd in tentativas:
    url = (f"{SERVICES}/org/{ORG}/filial/{fid}/centro_distribuicao/{cd}"
           f"/loja/buscas/produtos/termo/arroz?page=1")
    st, body = get(url)
    if st and st < 400 and '"produtos"' in body:
        try:
            d = json.loads(body)["data"]["produtos"]
            linha = ", ".join(f'{p["descricao"][:22]}={p["preco"]}' for p in d[:3])
            print(f"  filial={fid} cd={cd}  [{st}]  {linha}")
        except Exception:  # noqa: BLE001
            print(f"  filial={fid} cd={cd}  [{st}]  (json inesperado)")
    else:
        print(f"  filial={fid} cd={cd}  [{st}]  {body[:80]}")

secao("FIM - cola TODA a saída no chat")
