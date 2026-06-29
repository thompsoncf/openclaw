#!/usr/bin/env python3
"""Sonda de TOKEN v3 - mapeia as URLs de API do app e acha onde o token é SETADO.

Já sabemos: token é fetchado (não é fixo). Um interceptor põe 'sessao-id' +
'Authorization' (clienteTokenService.getToken()). Falta a URL do mint.

Esta sonda: (1) extrai TODOS os caminhos de API que aparecem no JS (o "mapa de
endpoints"); (2) imprime contexto LARGO onde o token é guardado (.setToken/
token=) e onde a sessão é carregada (.post/.get perto de sessao/cliente/token);
(3) acha os efeitos NgRx (ofType) ligados a Sessao/Token.

Roda:  python sonda_token3.py   -> cola TODA a saída. Stdlib pura, só lê.
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
        print("  (falha", url, e, ")"); return ""


def secao(t):
    print("\n" + "=" * 72 + f"\n{t}\n" + "=" * 72)


def ctx(JS, m, antes=90, depois=150):
    s = JS[max(0, m.start() - antes):m.end() + depois].replace("\n", " ")
    return re.sub(r"\s+", " ", s)


# baixa todos os chunks
html = pega(WEB + "/")
refs = re.findall(r'(?:src|href)=["\']([^"\']+\.js)["\']', html)
refs = sorted({u if u.startswith("http") else WEB + "/" + u.lstrip("/") for u in refs})
refs = [u for u in refs if "vlibras" not in u]
JS = ""
for u in refs:
    JS += pega(u) + "\n"
print(f"{len(refs)} chunks, {len(JS)} bytes")


# 1) MAPA de caminhos de API (strings e templates que parecem rota)
secao("1) MAPA de endpoints (caminhos de API achados no JS)")
paths = set()
# strings que começam com / e têm cara de api
for m in re.finditer(r'["\'`](/(?:api-admin/)?(?:v1/)?[a-z_]+[^"\'`]{0,90})["\'`]', JS):
    s = m.group(1)
    if any(k in s.lower() for k in ("org", "loja", "cliente", "sessa", "sesso", "token",
            "auth", "produto", "departamento", "busca", "filtro", "acesso", "config",
            "convidad", "anonim", "publico", "central", "filial")):
        paths.add(s)
# templates com ${...}
for m in re.finditer(r'`(/[^`]{3,110})`', JS):
    s = m.group(1)
    if "${" in s or s.count("/") >= 2:
        paths.add(s)
for s in sorted(paths):
    print("   ", s)


# 2) onde o TOKEN é guardado/lido e a SESSÃO é carregada
secao("2) contexto LARGO: token setado / sessão carregada / http calls")
for chave in ("setToken", "getToken", ".token=", "Token(", "sessaoService",
              "clienteTokenService", "getSessaoId", "getBaseUrl",
              "loadSessao", "carregarSessao", "iniciarSessao", "criarSessao"):
    achou = 0
    for m in re.finditer(re.escape(chave), JS):
        c = ctx(JS, m)
        if re.search(r'\.(post|get|put)\(|https?://|/v1|/org|/loja|/sessa|getBaseUrl|\$\{', c):
            print(f"\n   [{chave}]\n   ...{c}...")
            achou += 1
            if achou >= 3:
                break


# 3) efeitos NgRx (ofType) ligados a Sessao/Token + a chamada que disparam
secao("3) efeitos NgRx ligados a Sessao/Token")
for m in re.finditer(r'ofType\([^)]{0,60}\)', JS):
    around = ctx(JS, m, 30, 260)
    if re.search(r'[Ss]essao|[Tt]oken', around):
        print(f"\n   {around}")


# 4) toda chamada .post/.get cujo 1º arg (URL) tenha sessao/token/cliente/loja/acesso
secao("4) http calls com URL de sessao/token/cliente/acesso")
for m in re.finditer(r'\.(post|get|put)\(([^,)\n]{3,140})', JS):
    arg = m.group(2)
    low = arg.lower()
    if any(k in low for k in ("sessa", "sesso", "token", "/cliente", "acesso",
                              "convidad", "anonim", "publico", "/loja")):
        print(f"   .{m.group(1)}({re.sub(r'\\s+', ' ', arg)[:130]}")

secao("FIM - cola TODA a saída")
