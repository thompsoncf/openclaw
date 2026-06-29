"""scripts/sonda_rcarvalho.py

Descobre a config VipCommerce do R Carvalho (org, CDs, domainkey, token de
convidado) SEM precisar capturar cURL na mão. As SPAs da VipCommerce embutem o
'organizationid' e afins no HTML/bundles JS — esta sonda baixa essas páginas e
garimpa os padrões, e ainda tenta uns endpoints de bootstrap da API.

RODAR ONDE A REDE É ABERTA (Render Shell), não no sandbox:
    python scripts/sonda_rcarvalho.py

Cola a saída aqui que eu preencho o bloco r_carvalho no finance/catalogo_carvalho.py.
Não grava nada; só lê e imprime.
"""
import re
import ssl
import sys
import urllib.request
import urllib.error

CTX = ssl.create_default_context()
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/149.0 Safari/537.36")
BASE = "https://services.vipcommerce.com.br/api-admin/v1"

SITES = ["https://www.rcarvalhoonline.com.br/", "https://rcarvalhoonline.com.br/"]
DOMAINKEY = "rcarvalhoonline.com.br"


def _get(url, headers=None, timeout=20):
    req = urllib.request.Request(url, headers=headers or {"user-agent": UA})
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=timeout) as r:
            raw = r.read()
            return r.status, raw.decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "ignore")
        except Exception:  # noqa: BLE001
            body = ""
        return e.code, body
    except Exception as e:  # noqa: BLE001
        return None, f"ERRO: {e}"


def _achados(texto):
    """Extrai pistas de um blob de texto (HTML ou JS)."""
    out = {}
    org = re.findall(r'organization[iI]d["\']?\s*[:=]\s*["\']?(\d{1,5})', texto)
    org += re.findall(r'["\']org["\']\s*:\s*["\']?(\d{1,5})', texto)
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
        out["token(eyJ...)"] = [t[:60] + "..." for t in sorted(set(tok))][:3]
    return out


def varrer_site():
    print("=" * 70)
    print("1) HTML + bundles JS da SPA (org costuma estar embutido aqui)")
    print("=" * 70)
    achados_totais = {}
    html = None
    base_site = None
    for site in SITES:
        st, body = _get(site)
        print(f"\n[GET] {site} -> status {st} ({len(body)} bytes)")
        if st == 200 and "<" in body:
            html, base_site = body, site
            break
    if not html:
        print("  !! Nenhum site abriu. Tenta pelo celular/navegador e captura cURL.")
        return achados_totais

    # pistas no próprio HTML
    a = _achados(html)
    if a:
        print("  pistas no HTML:", a)
        achados_totais.update(a)

    # acha os <script src=...> e env/config
    srcs = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', html)
    srcs += re.findall(r'["\'](/[^"\']*\.js)["\']', html)
    srcs = list(dict.fromkeys(srcs))
    # prioriza bundles que tendem a ter config
    pri = [s for s in srcs if re.search(r'(env|config|main|app|runtime|chunk|vendor)', s, re.I)]
    resto = [s for s in srcs if s not in pri]
    alvos = (pri + resto)[:12]
    print(f"\n  {len(srcs)} scripts achados; inspecionando {len(alvos)}:")
    from urllib.parse import urljoin
    for s in alvos:
        url = urljoin(base_site, s)
        st, body = _get(url)
        a = _achados(body)
        flag = "  <-- PISTA!" if a else ""
        print(f"   [{st}] {url[:80]}{flag}")
        if a:
            print("        ", a)
            for k, v in a.items():
                achados_totais.setdefault(k, [])
                for it in v:
                    if it not in achados_totais[k]:
                        achados_totais[k].append(it)
    return achados_totais


def tentar_api():
    print("\n" + "=" * 70)
    print("2) endpoints de bootstrap da API (com domainkey, sem org)")
    print("=" * 70)
    headers = {
        "accept": "application/json", "user-agent": UA,
        "domainkey": DOMAINKEY, "origin": f"https://www.{DOMAINKEY}",
        "referer": f"https://www.{DOMAINKEY}/",
    }
    candidatos = [
        f"{BASE}/loja/configuracoes",
        f"{BASE}/configuracoes",
        f"{BASE}/organizacao",
        f"{BASE}/loja",
        f"{BASE}/org",
        f"{BASE}/loja/dados",
        f"{BASE}/estabelecimento",
    ]
    for url in candidatos:
        st, body = _get(url, headers=headers)
        a = _achados(body) if isinstance(body, str) else {}
        snippet = (body[:140].replace("\n", " ") if isinstance(body, str) else str(body))
        print(f"\n[GET] {url}\n   status {st} | {snippet}")
        if a:
            print("   PISTA:", a)


def main():
    print("SONDA R CARVALHO (VipCommerce) — descoberta de org/CDs/token")
    print("(rode no Render Shell; o sandbox do assistente não alcança esses domínios)\n")
    tot = varrer_site()
    ttry_api = "--no-api" not in sys.argv
    if ttry_api:
        tentar_api()
    print("\n" + "=" * 70)
    print("RESUMO (cola tudo isto na conversa):")
    print("=" * 70)
    print(tot if tot else "  (nenhuma pista automática — manda o cURL do Network)")


if __name__ == "__main__":
    main()
