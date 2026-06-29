"""scripts/sonda_vip.py

Sonda GENÉRICA pra achar a config VipCommerce (org, CDs, domainkey, token) de uma
loja cujo SITE ABRE (ex.: ferreiraemcasa.com.br). Lê o HTML + bundles JS e garimpa
os padrões que a SPA deixa embutidos. Também confirma via API.

USO (no Render Shell, rede aberta):
    python scripts/sonda_vip.py ferreiraemcasa.com.br
    python scripts/sonda_vip.py www.ferreiraemcasa.com.br

Só lê e imprime. Cola a saída aqui que eu monto o bloco da rede no
finance/catalogo_carvalho.py.
"""
import re
import ssl
import sys
import urllib.request
import urllib.error
from urllib.parse import urljoin

CTX = ssl.create_default_context()
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/149.0 Safari/537.36")
BASE = "https://services.vipcommerce.com.br/api-admin/v1"


def _get(url, headers=None, timeout=20):
    req = urllib.request.Request(url, headers=headers or {"user-agent": UA})
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read().decode("utf-8", "ignore")
        except Exception:  # noqa: BLE001
            return e.code, ""
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


def _achados(texto):
    out = {}
    org = re.findall(r'organization[iI]d["\']?\s*[:=]\s*["\']?(\d{1,5})', texto)
    org += re.findall(r'["\']org["\']\s*:\s*["\']?(\d{1,5})', texto)
    org += re.findall(r'/org/(\d{1,5})/filial', texto)
    if org:
        out["org"] = sorted(set(org), key=lambda x: (len(x), x))
    dom = re.findall(r'domain[kK]ey["\']?\s*[:=]\s*["\']([\w.\-]+)["\']', texto)
    if dom:
        out["domainkey"] = sorted(set(dom))
    cds = re.findall(r'centro_distribuicao/(\d{1,4})', texto)
    cds += re.findall(r'centro[_A-Za-z]*[dD]istribuicao["\']?\s*[:=]\s*["\']?(\d{1,4})', texto)
    if cds:
        out["cds"] = sorted(set(cds), key=int)
    fil = re.findall(r'filial["\']?\s*[:=]\s*["\']?(\d{1,4})', texto)
    if fil:
        out["filial"] = sorted(set(fil), key=int)
    tok = re.findall(r'(eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,})', texto)
    if tok:
        out["token"] = sorted(set(tok))
    return out


def merge(tot, a):
    for k, v in a.items():
        tot.setdefault(k, [])
        for it in v:
            if it not in tot[k]:
                tot[k].append(it)


def main():
    if len(sys.argv) < 2:
        print("uso: python scripts/sonda_vip.py <dominio>  (ex.: ferreiraemcasa.com.br)")
        return
    dom = sys.argv[1].strip().replace("https://", "").replace("http://", "").strip("/")
    base_dom = dom[4:] if dom.startswith("www.") else dom
    sites = [f"https://www.{base_dom}/", f"https://{base_dom}/",
             f"https://www.{base_dom}/produtos", f"https://www.{base_dom}/loja"]
    print(f"SONDA VIP — {base_dom}\n" + "=" * 64)

    tot = {}
    html, base_site = None, None
    for s in sites:
        st, body = _get(s)
        print(f"[GET] {s} -> {st} ({len(body) if isinstance(body, str) else 0} bytes)")
        if st == 200 and isinstance(body, str) and "<" in body and not html:
            html, base_site = body, s

    if html:
        merge(tot, _achados(html))
        srcs = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', html)
        srcs += re.findall(r'["\'](/[^"\']*\.js)["\']', html)
        srcs = list(dict.fromkeys(srcs))
        pri = [s for s in srcs if re.search(r'(env|config|main|app|runtime|chunk|vendor|polyfill)', s, re.I)]
        alvos = (pri + [s for s in srcs if s not in pri])[:15]
        print(f"\n{len(srcs)} scripts; inspecionando {len(alvos)} bundles:")
        for s in alvos:
            url = urljoin(base_site, s)
            st, body = _get(url)
            a = _achados(body) if isinstance(body, str) else {}
            print(f"  [{st}] {url[:78]}{'  <-- PISTA' if a else ''}")
            if a:
                show = {k: (v if k != 'token' else [t[:50]+'...' for t in v]) for k, v in a.items()}
                print("       ", show)
                merge(tot, a)
    else:
        print("\n!! site não abriu — manda o cURL do Network.")

    # confirma via API se achamos org
    if tot.get("org"):
        print("\nConfirmando org via API (/loja/configuracoes):")
        for org in tot["org"][:4]:
            h = {"accept": "application/json", "user-agent": UA,
                 "domainkey": base_dom, "organizationid": str(org),
                 "origin": f"https://www.{base_dom}", "referer": f"https://www.{base_dom}/"}
            st, body = _get(f"{BASE}/loja/configuracoes", headers=h)
            print(f"  org={org} -> {st} | {(body[:160].replace(chr(10), ' ') if isinstance(body, str) else body)}")

    print("\n" + "=" * 64 + "\nRESUMO (cola tudo):")
    if tot:
        for k, v in tot.items():
            if k == "token":
                print(f"  {k}: {[t[:55]+'...' for t in v]}")
            else:
                print(f"  {k}: {v}")
        print("\n  (se aparecer 'token', cola ele INTEIRO — preciso completo)")
    else:
        print("  (nada — manda o cURL do Network)")


if __name__ == "__main__":
    main()
