#!/usr/bin/env python3
"""Sonda de TOKEN v2 - descobre se o Bearer é FIXO (embutido no JS) ou FETCHADO.

O mesmo token funcionou por dias -> forte indício de que é estático/cacheado.
Esta sonda procura: (1) qualquer JWT literal dentro do JS (eyJ...), (2) o env
config (apiKey/baseUrl), (3) onde 'setVipToken'/'token=' acontece e qual URL a
sessão/token usa.

Roda:  python sonda_token2.py  -> cola a saída. Stdlib pura, só lê.
"""
import re
import ssl
import urllib.request

WEB = "https://www.carvalhosupershop.com.br"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/149.0 Safari/537.36")
_CTX = ssl.create_default_context()


def pega(url):
    try:
        req = urllib.request.Request(url, headers={"user-agent": UA, "accept-encoding": "identity"})
        with urllib.request.urlopen(req, timeout=25, context=_CTX) as r:
            return r.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        print("  (falha", url, e, ")")
        return ""


def secao(t):
    print("\n" + "=" * 70 + f"\n{t}\n" + "=" * 70)


# baixa todos os chunks
html = pega(WEB + "/")
refs = re.findall(r'(?:src|href)=["\']([^"\']+\.js)["\']', html)
refs = sorted({u if u.startswith("http") else WEB + "/" + u.lstrip("/") for u in refs})
refs = [u for u in refs if "vlibras" not in u]
JS = ""
for u in refs:
    JS += pega(u) + "\n"
print(f"{len(refs)} chunks, {len(JS)} bytes")


# 1) JWT literais embutidos (token público fixo?)
secao("1) JWT literais dentro do JS (eyJ...) - se aparecer, o token é FIXO")
jwts = set(re.findall(r'eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{6,}', JS))
if jwts:
    for j in jwts:
        print("   ACHOU JWT:", j[:80], "...")
else:
    print("   (nenhum JWT literal - então o token é FETCHADO de algum endpoint)")


# 2) env config (apiKey / baseUrl / urls de api)
secao("2) env config (apiKey / baseUrl / hosts de api)")
for m in re.finditer(r'(apiKey|api_key|baseUrl|baseURL|servicesUrl|apiUrl|environment|production)\s*[:=]\s*["\']?([^"\',;}]{4,80})', JS):
    print(f"   {m.group(1)} = {m.group(2)[:70]}")


# 3) onde o token é SETADO / a sessão é carregada (URL do fetch)
secao("3) contexto de setVipToken / token= / sessao / loadSessao / http call")
for chave in ("setVipToken", "VipToken=", "vipToken", "loadSessao", "carregarSessao",
              "Sessao", "criarSessao", "sessoes", "obterToken", "gerarToken", "acesso"):
    achou = 0
    for m in re.finditer(re.escape(chave), JS):
        ctx = JS[max(0, m.start() - 70):m.end() + 110].replace("\n", " ")
        ctx = re.sub(r"\s+", " ", ctx)
        # só mostra se tiver cara de url/http perto
        if re.search(r'https?://|/v1|/api|\.post\(|\.get\(|\$\{', ctx):
            print(f"   [{chave}] ...{ctx}...")
            achou += 1
            if achou >= 2:
                break


# 4) qualquer template de URL com sessao/token/acesso/publico/anonimo
secao("4) templates de URL suspeitos (sessao/token/acesso/publico/anonimo)")
vis = set()
for m in re.finditer(r'`([^`]{4,140})`|"(/[^"]{4,120})"|\'(/[^\']{4,120})\'', JS):
    s = (m.group(1) or m.group(2) or m.group(3) or "")
    low = s.lower()
    if "/" in s and any(k in low for k in ("sessa", "sesso", "/token", "acesso", "publico",
                                            "anonim", "convidad", "/auth", "guest", "vip")):
        if "firebase" not in low and "fcm" not in low and s not in vis:
            vis.add(s)
            print("   ", s)

secao("FIM - cola TODA a saída")
