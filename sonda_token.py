#!/usr/bin/env python3
"""Sonda de TOKEN - acha como o app VipCommerce do Carvalho EMITE o token de
convidado (pra o cron renovar sozinho, sem recaptura manual).

Faz 2 coisas:
  A) baixa TODOS os chunks JS e procura a rota de emissão (getVipToken /
     lojaTokenService / 'auth' / 'sessao' / 'convidado' / 'token').
  B) tenta endpoints de mint prováveis (sem auth, só domainkey) e vê se algum
     devolve um JWT (eyJ...).

Roda:  python sonda_token.py   -> cola a saída.
Só lê / tenta. Stdlib pura.
"""
import json
import re
import ssl
import urllib.error
import urllib.request

WEB = "https://www.carvalhosupershop.com.br"
BASE = "https://services.vipcommerce.com.br/api-admin/v1"
ORG = "23"
DOMAINKEY = "carvalhosupershop.com.br"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/149.0 Safari/537.36")
_CTX = ssl.create_default_context()


def pega(url, metodo="GET", corpo=None, headers=None):
    h = {"user-agent": UA, "accept": "*/*", "accept-encoding": "identity"}
    if headers:
        h.update(headers)
    data = corpo.encode() if isinstance(corpo, str) else corpo
    try:
        req = urllib.request.Request(url, data=data, headers=h, method=metodo)
        with urllib.request.urlopen(req, timeout=25, context=_CTX) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read(800).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            return e.code, ""
    except Exception as e:  # noqa: BLE001
        return None, f"ERRO: {e}"


def secao(t):
    print("\n" + "=" * 70 + f"\n{t}\n" + "=" * 70)


# ---------------------------------------- A) varre TODOS os chunks JS
secao("A) procurando a rota de emissão do token nos chunks JS")
_, html = pega(WEB + "/")
js_refs = re.findall(r'(?:src|href)=["\']([^"\']+\.js)["\']', html)
js_refs = sorted({u if u.startswith("http") else WEB + "/" + u.lstrip("/") for u in js_refs})
js_refs = [u for u in js_refs if "vlibras" not in u]
print(f"{len(js_refs)} chunks JS")

JS = ""
for u in js_refs:
    _, body = pega(u)
    JS += body + "\n"
print(f"bytes JS total: {len(JS)}")

# fragmentos de string que parecem rota de auth/sessao/token (ignora firebase fcm)
frag = set()
for m in re.finditer(r'`([^`]{3,120})`|"([^"]{3,100})"|\'([^\']{3,100})\'', JS):
    s = (m.group(1) or m.group(2) or m.group(3) or "")
    if "/" not in s:
        continue
    low = s.lower()
    if any(k in low for k in ("auth", "sessao", "session", "token", "convidado", "login", "guest")):
        if "firebase" in low or "fcm" in low or "googleapis" in low:
            continue
        frag.add(s.strip())
print("-- fragmentos de rota candidatos:")
for s in sorted(frag):
    print("   ", s)

# contexto ao redor de getVipToken / lojaToken / getBaseUrl (onde a URL é montada)
print("\n-- contexto de getVipToken / lojaToken / getBaseUrl / .post(:")
for chave in ("getVipToken", "lojaToken", "VipToken", "getBaseUrl", "autenticar", "convidado"):
    for m in re.finditer(re.escape(chave), JS):
        ctx = JS[max(0, m.start() - 60):m.end() + 90].replace("\n", " ")
        ctx = re.sub(r"\s+", " ", ctx)
        print(f"   [{chave}] ...{ctx}...")
        break  # 1 exemplo por chave


# ---------------------------------------- B) tenta endpoints de mint
secao("B) tentando endpoints de emissão (sem auth, só domainkey)")
H = {"accept": "application/json", "content-type": "application/json",
     "domainkey": DOMAINKEY, "organizationid": ORG,
     "origin": WEB, "referer": WEB + "/"}
CANDS = [
    ("POST", f"{BASE}/auth"),
    ("POST", f"{BASE}/sessao"),
    ("POST", f"{BASE}/autenticacao"),
    ("POST", f"{BASE}/token"),
    ("POST", f"{BASE}/loja/auth"),
    ("POST", f"{BASE}/loja/sessao"),
    ("POST", f"{BASE}/org/{ORG}/auth"),
    ("POST", f"{BASE}/org/{ORG}/sessao"),
    ("POST", f"{BASE}/org/{ORG}/loja/auth"),
    ("POST", f"{BASE}/org/{ORG}/loja/sessao"),
    ("POST", f"{BASE}/org/{ORG}/loja/convidado"),
    ("POST", f"{BASE}/org/{ORG}/cliente/convidado"),
    ("GET",  f"{BASE}/org/{ORG}/loja/dados"),
    ("POST", f"{BASE}/loja/convidado"),
    ("POST", f"{BASE}/convidado"),
]
for metodo, url in CANDS:
    st, body = pega(url, metodo=metodo, corpo="{}", headers=H)
    tem_jwt = "eyJ" in (body or "")
    marca = "  <<< TEM JWT!" if tem_jwt else ""
    print(f"  [{st}] {metodo} {url.replace(BASE,'')}{marca}")
    if tem_jwt:
        m = re.search(r'(eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+)', body)
        print("       token:", (m.group(1)[:60] + "...") if m else body[:120])

secao("FIM - cola TODA a saída")
