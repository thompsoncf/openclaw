#!/usr/bin/env python3
"""Sonda de CATALOGO - reconhecimento do Carvalho Supershop (carvalhosupershop.com.br).

NAO faz parte do sistema. É uma missão de reconhecimento, no mesmo espírito das
sonda_sefaz: roda NO RENDER (rede liberada), descobre QUAL endpoint JSON a loja
usa pra carregar produto+preço, com QUAIS headers/chaves, e mostra uma amostra.
Com isso em mãos, construímos o ingestor de verdade (finance/).

Como rodar no Render:
  - git push deste arquivo.
  - No Shell do serviço openclaw-web:  python sonda_catalogo.py
  - Copie TODA a saída e cole no chat.

Só lê dados públicos de preço. Não envia nada, não muda nada. Stdlib pura.

Estratégia (3 fases):
  1) Pega o HTML e extrai os bundles JS (a SPA carrega tudo via JS).
  2) Varre os bundles atrás de: base de API, paths /produtos|/search|graphql,
     domínio de vendor (instabuy/vipcommerce/mercadapp/...) e CHAVES de loja
     (apikey, domainkey, uuid, organization, x-store...). Aqui mora o ouro.
  3) Tenta uma lista de endpoints prováveis (na própria origem e nos vendors
     conhecidos) buscando 'arroz' e mostra status + amostra + se veio JSON.
"""
import json
import re
import ssl
import urllib.request
import urllib.error
from urllib.parse import urljoin, urlparse

BASE = "https://www.carvalhosupershop.com.br"
TERMO = "arroz"

_H = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "*/*",
    "Accept-Language": "pt-BR,pt;q=0.9",
    "Accept-Encoding": "identity",   # evita gzip pra não precisar descomprimir
    "Referer": BASE + "/",
    "Origin": BASE,
}
_CTX = ssl.create_default_context()


def pega(url, extra=None, metodo="GET", corpo=None, timeout=20):
    """GET/POST tolerante. Retorna (status, content_type, texto). Nunca quebra."""
    headers = dict(_H)
    if extra:
        headers.update(extra)
    data = corpo.encode("utf-8") if isinstance(corpo, str) else corpo
    try:
        req = urllib.request.Request(url, headers=headers, data=data, method=metodo)
        with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
            ct = r.headers.get("Content-Type", "")
            raw = r.read(2_000_000)  # cap 2MB
            return r.status, ct, raw.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            body = e.read(4000).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            body = ""
        return e.code, e.headers.get("Content-Type", "") if e.headers else "", body
    except Exception as e:  # noqa: BLE001
        return None, "", f"ERRO: {e}"


def secao(t):
    print("\n" + "=" * 70 + f"\n{t}\n" + "=" * 70)


# ---------------------------------------------------------------- FASE 1: HTML
secao("FASE 1 - HTML base + bundles JS")
st, ct, html = pega(BASE + "/")
print(f"GET / -> status={st} ct={ct} bytes={len(html)}")

scripts = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', html, re.I)
scripts = [urljoin(BASE + "/", s) for s in scripts]
# também pega <link rel=preload as=script> e modulepreload
scripts += [urljoin(BASE + "/", s) for s in
            re.findall(r'<link[^>]+(?:as=["\']script["\']|rel=["\']modulepreload["\'])[^>]+href=["\']([^"\']+)["\']', html, re.I)]
scripts = sorted(set(scripts))
print(f"{len(scripts)} bundle(s) JS encontrados:")
for s in scripts:
    print("  -", s)

# pistas inline no HTML (config injetada, env, etc.)
for m in re.findall(r'(window\.[A-Z_]+\s*=\s*\{[^<]{0,400})', html):
    print("  [inline]", m[:200])


# ------------------------------------------------------- FASE 2: varre bundles
secao("FASE 2 - varredura dos bundles JS (base de API, paths, vendor, CHAVES)")

VENDORS = ["instabuy", "vipcommerce", "mercadapp", "b8one", "bewer", "deliveryapp",
           "supermercadosonline", "neomode", "linximpulse", "vipcom", "ifood",
           "rappi", "magazord", "linx", "shopfacil", "bevcommerce"]
RE_URL = re.compile(r'https?://[A-Za-z0-9._\-]+(?:/[A-Za-z0-9._/\-]*)?')
RE_PATH = re.compile(r'["\'](/(?:api|v\d|loja|produtos?|products?|search|busca|catalog[oa]?|graphql)[A-Za-z0-9._/\-]*)["\']')
RE_KEYS = re.compile(r'(api[_-]?key|apikey|domain[_-]?key|domainkey|x-?store|organization|'
                     r'estabelecimento|loja[_-]?id|token|authorization|x-?api|uuid|tenant)'
                     r'["\']?\s*[:=]\s*["\']?([A-Za-z0-9\-_.]{6,60})', re.I)

api_hosts, api_paths, vendor_hits, key_hits = set(), set(), set(), set()

for s in scripts:
    st2, ct2, body = pega(s)
    if not body or st2 is None:
        print(f"  (pulo) {s} -> {st2}")
        continue
    print(f"  varrendo {s.split('/')[-1]} ({len(body)} bytes)")
    for u in RE_URL.findall(body):
        host = urlparse(u).netloc
        if host and "carvalho" not in host and "google" not in host and "gstatic" not in host \
                and "fonts" not in host and "facebook" not in host and "sentry" not in host:
            api_hosts.add(host)
    for p in RE_PATH.findall(body):
        api_paths.add(p.split("?")[0][:60])
    low = body.lower()
    for v in VENDORS:
        if v in low:
            vendor_hits.add(v)
    for k, val in RE_KEYS.findall(body):
        key_hits.add(f"{k.lower()} = {val}")

print("\n-- HOSTS externos (candidatos a API):")
for h in sorted(api_hosts):
    print("   ", h)
print("\n-- PATHS candidatos (produto/busca/catalogo/graphql):")
for p in sorted(api_paths):
    print("   ", p)
print("\n-- VENDOR/plataforma detectada:", ", ".join(sorted(vendor_hits)) or "(nenhum conhecido)")
print("\n-- CHAVES/identificadores suspeitos (apikey/domainkey/loja/uuid/token):")
for k in sorted(key_hits):
    print("   ", k)


# ------------------------------------------------ FASE 3: prova de endpoints
secao(f"FASE 3 - tenta puxar produtos (termo='{TERMO}')")

# monta candidatos: origem própria + qualquer host externo achado, com paths comuns
PATHS_COMUNS = [
    f"/api/produtos?busca={TERMO}", f"/api/produtos?q={TERMO}", f"/api/produtos?termo={TERMO}",
    f"/api/v1/produtos?busca={TERMO}", f"/api/v1/produtos/busca?termo={TERMO}",
    f"/api/products?search={TERMO}", f"/api/search?q={TERMO}", f"/api/busca?termo={TERMO}",
    f"/produtos?busca={TERMO}", f"/api/catalogo/produtos?busca={TERMO}",
    f"/v1/search?query={TERMO}", f"/v2/search?query={TERMO}",
]
origens = [BASE] + sorted({"https://" + h for h in api_hosts})
candidatos = [o + p for o in origens for p in PATHS_COMUNS]

# pares chave de header pra tentar (caso ache domainkey/apikey nas bundles)
extra_headers_tentativas = [None]
for k in sorted(key_hits):
    nome, _, valor = k.partition(" = ")
    if "domain" in nome:
        extra_headers_tentativas.append({"DomainKey": valor, "x-domain-key": valor})
    elif "api" in nome or "token" in nome:
        extra_headers_tentativas.append({"x-api-key": valor, "Authorization": "Bearer " + valor})

achou = 0
vistos = set()
for url in candidatos:
    if url in vistos:
        continue
    vistos.add(url)
    for eh in extra_headers_tentativas:
        st3, ct3, body = pega(url, extra=eh)
        eh_json = "json" in (ct3 or "").lower() or body[:1].strip() in "[{"
        if st3 and st3 < 400 and eh_json and len(body) > 40:
            achou += 1
            tag = "  ".join(f"{k}" for k in (eh or {}).keys()) or "sem-header-extra"
            print(f"\n  >>> POSSIVEL HIT [{tag}] {url}")
            print(f"      status={st3} ct={ct3} bytes={len(body)}")
            # tenta achar nome/preço na amostra
            amostra = body[:600].replace("\n", " ")
            print("      amostra:", amostra)
            precos = re.findall(r'"(?:preco|price|valor|preco_venda|vlVenda)"\s*:\s*"?\d', body)
            if precos:
                print(f"      >> tem campo de PRECO ({len(precos)} ocorrências) - bom sinal!")
            break  # achou com algum header, não precisa testar os outros
        elif st3 and st3 in (401, 403):
            print(f"  (auth {st3}) {url}  [header: {list((eh or {}).keys()) or 'nenhum'}]")

if not achou:
    print("\n  Nenhum endpoint JSON respondeu produto direto.")
    print("  -> Olhe os HOSTS e PATHS da FASE 2 e me mande: provavelmente o produto")
    print("     carrega via POST/GraphQL ou exige um header de loja específico.")
    print("  -> Dica: no navegador, F12 > Network > filtre 'Fetch/XHR' e busque 'arroz';")
    print("     a 1ª chamada que devolve a lista é o endpoint. Cole a URL aqui.")

secao("FIM - cole TODA a saída acima no chat")
