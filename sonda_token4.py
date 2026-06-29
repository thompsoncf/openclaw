#!/usr/bin/env python3
"""Sonda de TOKEN v4 - confirma a AUTO-EMISSÃO do token.

Descoberta da v3: o servidor renova o token num header de resposta 'X-Auth-Upgrade'
(this.clienteTokenService.setToken(resp.headers['X-Auth-Upgrade'])). E há um
endpoint 'criar_token' na config.

Esta sonda: faz requisições SEM Authorization e imprime TODOS os headers da
resposta - se 'X-Auth-Upgrade' (ou qualquer header com eyJ...) aparecer, achamos
o mint automático. Também busca a config /loja procurando 'criar_token'.

Roda:  python sonda_token4.py   -> cola TODA a saída. Stdlib pura.
"""
import json
import re
import ssl
import urllib.error
import urllib.request
import uuid

BASE = "https://services.vipcommerce.com.br/api-admin/v1"
WEB = "https://www.carvalhosupershop.com.br"
ORG = "23"
DOMAINKEY = "carvalhosupershop.com.br"
SESS = uuid.uuid4().hex  # sessão nova, sem token
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/149.0 Safari/537.36")
_CTX = ssl.create_default_context()


def base_headers():
    return {
        "accept": "application/json",
        "content-type": "application/json",
        "domainkey": DOMAINKEY,
        "organizationid": ORG,
        "sessao-id": SESS,
        "origin": WEB,
        "referer": WEB + "/",
        "user-agent": UA,
    }


def chamar(metodo, url, com_auth_vazio=False, corpo=None):
    h = base_headers()
    if com_auth_vazio:
        h["authorization"] = ""  # sem token, como o 1º request do app
    data = corpo.encode() if isinstance(corpo, str) else corpo
    try:
        req = urllib.request.Request(url, data=data, headers=h, method=metodo)
        with urllib.request.urlopen(req, timeout=25, context=_CTX) as r:
            return r.status, dict(r.headers), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), (e.read(1500).decode("utf-8", "replace"))
    except Exception as ex:  # noqa: BLE001
        return None, {}, f"ERRO: {ex}"


def mostra_headers(hdrs):
    achou = None
    for k, v in hdrs.items():
        kl = k.lower()
        flag = ""
        if "auth" in kl or "token" in kl or "upgrade" in kl or "eyJ" in str(v):
            flag = "   <<<<<"
            if "eyJ" in str(v) or "upgrade" in kl or "token" in kl:
                achou = (k, v)
        print(f"      {k}: {str(v)[:80]}{flag}")
    return achou


def secao(t):
    print("\n" + "=" * 72 + f"\n{t}\n" + "=" * 72)


# 1) a busca que funciona, SEM Authorization -> o servidor devolve X-Auth-Upgrade?
secao("1) BUSCA sem Authorization (espera X-Auth-Upgrade na resposta)")
url_busca = f"{BASE}/org/{ORG}/filial/1/centro_distribuicao/1/loja/buscas/produtos/termo/arroz?page=1&session={uuid.uuid4()}"
for tag, kw in (("sem header authorization", False), ("authorization vazio", True)):
    st, hdrs, body = chamar("GET", url_busca, com_auth_vazio=kw)
    print(f"\n  -- {tag}: status={st}")
    achou = mostra_headers(hdrs)
    if achou:
        print(f"  >>> TOKEN no header '{achou[0]}': {str(achou[1])[:70]}...")
    if "eyJ" in (body or ""):
        m = re.search(r'eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+', body)
        print(f"  >>> JWT no CORPO: {m.group(0)[:60]}...")


# 2) config /loja e variantes - procura 'criar_token' / url de token
secao("2) config da loja (procura 'criar_token' / token url)")
for url in (f"{BASE}/org/{ORG}/loja",
            f"{BASE}/org/{ORG}/filial/1/loja",
            f"{BASE}/org/{ORG}",
            f"{BASE}/loja"):
    st, hdrs, body = chamar("GET", url, com_auth_vazio=True)
    print(f"\n  -- GET {url.replace(BASE,'')}: status={st}")
    mostra_headers(hdrs)
    if body and "ERRO" not in body[:6]:
        for kw in ("criar_token", "token", "sessao", "auth"):
            for m in re.finditer(kw, body):
                trecho = body[max(0, m.start() - 30):m.end() + 60].replace("\n", " ")
                print(f"     [{kw}] ...{trecho}...")
                break

secao("FIM - cola TODA a saída")
