#!/usr/bin/env python3
"""Sonda de ROTAS - lê o mapa de endpoints direto do JS do app (determinista).

O app Angular guarda as rotas da API como texto (template strings). Em vez de
adivinhar endpoint, a gente extrai todas. Foco: token/sessão (emissão do token),
departamentos/seções (catálogo inteiro), cep/loja (nome/endereço), centro_distribuição.

Roda:  python sonda_rotas.py   -> cola a saída.
Só lê JS público. Stdlib pura, sem auth.
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
        print("  (falha", url, ":", e, ")")
        return ""


def secao(t):
    print("\n" + "=" * 70 + f"\n{t}\n" + "=" * 70)


# 1) bundles
html = pega(WEB + "/")
bundles = re.findall(r'src=["\']([^"\']+\.js)["\']', html)
bundles = [b if b.startswith("http") else WEB + "/" + b.lstrip("/") for b in bundles]
bundles = [b for b in bundles if "vlibras" not in b and "polyfill" not in b]
print("bundles:", len(bundles))

# 2) junta o JS todo
JS = ""
for b in bundles:
    JS += pega(b) + "\n"
print("bytes JS:", len(JS))

# 3) extrai TODAS as strings (aspas simples, duplas, crase) que parecem path de API
KEY = re.compile(r'(org|loja|filial|centro_distribuicao|departamento|secao|secoes|'
                 r'produto|busca|sessao|token|convidado|auth|login|cep|entrega|'
                 r'filiais|categoria|ofertas|cliente)', re.I)
PATHLIKE = re.compile(r'[A-Za-z0-9_${}/\-.:]{4,140}')

achados = set()
for m in re.finditer(r'`([^`]{3,160})`|"([^"]{3,140})"|\'([^\']{3,140})\'', JS):
    s = m.group(1) or m.group(2) or m.group(3)
    if "/" not in s:
        continue
    if not KEY.search(s):
        continue
    # limpa: tem que ter cara de rota (barra + segmento), não frase
    if " " in s and "${" not in s:
        continue
    achados.add(s.strip())

# 4) classifica por interesse
def grupo(rx):
    rx = re.compile(rx, re.I)
    return sorted({a for a in achados if rx.search(a)})

secao("ROTAS DE AUTH / TOKEN / SESSAO (emissão do token p/ o cron)")
for a in grupo(r'token|sessao|convidado|auth|login|cliente'):
    print("  ", a)

secao("ROTAS DE CATALOGO (departamentos / seções / produtos)")
for a in grupo(r'departamento|secao|secoes|categoria|produto|busca|ofertas'):
    print("  ", a)

secao("ROTAS DE LOJA / CEP / FILIAL / CD (nome+endereço+entrega)")
for a in grupo(r'cep|filial|filiais|centro_distribuicao|entrega|loja'):
    print("  ", a)

secao("OUTRAS rotas com /org/ ou base de API")
for a in grupo(r'/org/|api-admin|services\.vip|getBaseUrl|environment'):
    print("  ", a)

secao(f"FIM - {len(achados)} rotas únicas. Cola TODA a saída.")
