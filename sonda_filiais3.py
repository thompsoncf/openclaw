#!/usr/bin/env python3
"""Sonda de FILIAIS v3 - cURL embutido verbatim + autoteste do token.

Roda direto, sem curl.txt:  python sonda_filiais3.py  -> cola a saída.

Faz: (0) decodifica o JWT e mostra org/iat pra confirmar que o token está íntegro;
(A) tenta listar filiais/CD; (B) roda 'arroz' em várias filiais pra provar que o
preço muda. Só lê. Stdlib pura.

Se a saída disser 'token expirado/inválido': recapture um cURL novo no navegador
e troque o AUTHORIZATION + SESSION + SESSAO_ID abaixo (só essas 3 linhas).
"""
import base64
import json
import re
import ssl
import time
import urllib.request
import urllib.error

# ============ CREDENCIAIS (verbatim do cURL do navegador) ====================
ORG = "23"
SESSION = "a2f4128f-1700-4b42-8916-86620e24773d"   # query ?session=
AUTHORIZATION = "Bearer eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJpc3MiOiJ2aXBjb21tZXJjZSIsImF1ZCI6ImFwaS1hZG1pbiIsInN1YiI6IjZiYzQ4NjdlLWRjYTktMTFlOS04NzQyLTAyMGQ3OTM1OWNhMCIsInZpcGNvbW1lcmNlQ2xpZW50ZUlkIjpudWxsLCJpYXQiOjE3ODI2MTM2NDMsInZlciI6MSwiY2xpZW50IjpudWxsLCJvcGVyYXRvciI6bnVsbCwib3JnIjoiMjMifQ.Z9g-iSiCX20vKyBITiDUEhhjDWxiQTkJE8zopvhVc3zC2pUwaMZ9tACLD4tI3s_ow1LXtp467HUxi002_qGzog"

BASE = "https://services.vipcommerce.com.br/api-admin/v1"
HEADERS = {
    "accept": "application/json",
    "accept-language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "authorization": AUTHORIZATION,
    "content-type": "application/json",
    "domainkey": "carvalhosupershop.com.br",
    "organizationid": ORG,
    "origin": "https://www.carvalhosupershop.com.br",
    "referer": "https://www.carvalhosupershop.com.br/",
    "sessao-id": "60be7ce826dc93b0ff32c8e43fb585d8",
    "user-agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"),
}
_CTX = ssl.create_default_context()


def secao(t):
    print("\n" + "=" * 70 + f"\n{t}\n" + "=" * 70)


def get(url, timeout=20):
    sep = "&" if "?" in url else "?"
    full = f"{url}{sep}session={SESSION}"
    try:
        req = urllib.request.Request(full, headers=HEADERS)
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


# --------------------------------------------- 0) autoteste do token
secao("0) AUTOTESTE DO TOKEN (sem rede)")
try:
    payload_b64 = AUTHORIZATION.split(".")[1]
    payload_b64 += "=" * (-len(payload_b64) % 4)
    payload = json.loads(base64.urlsafe_b64decode(payload_b64))
    print("  payload do JWT:", json.dumps(payload, ensure_ascii=False))
    iat = payload.get("iat")
    if iat:
        idade_h = (time.time() - iat) / 3600
        print(f"  emitido há ~{idade_h:.1f} h  | org no token = {payload.get('org')}")
        if payload.get("org") != ORG:
            print("  !! ATENÇÃO: org do token != ORG configurado")
    print("  -> token decodificou OK (íntegro). Validade real só a API dirá abaixo.")
except Exception as e:  # noqa: BLE001
    print("  !! TOKEN CORROMPIDO (não decodificou):", e)
    print("     Recapture o cURL e troque AUTHORIZATION.")


# --------------------------------------------- A) listar filiais/CD
secao("A) LISTAR filiais / centros de distribuição")
CANDIDATOS = [
    f"{BASE}/org/{ORG}/loja",
    f"{BASE}/org/{ORG}/loja/filiais",
    f"{BASE}/org/{ORG}/filiais",
    f"{BASE}/org/{ORG}/loja/centros_distribuicao",
    f"{BASE}/org/{ORG}/centros_distribuicao",
    f"{BASE}/org/{ORG}/loja/dados",
    f"{BASE}/org/{ORG}/loja/configuracoes",
    f"{BASE}/org/{ORG}/filial/1/centro_distribuicao/1/loja",
    f"{BASE}/org/{ORG}/filial/1/centro_distribuicao/1/loja/filiais",
    f"{BASE}/org/{ORG}/filial/1/centro_distribuicao/1/loja/enderecos/filiais",
]
lista = None
for u in CANDIDATOS:
    st, body = get(u)
    eh_json = body[:1].strip() in "[{"
    flag = ""
    if st and st < 400 and eh_json and re.search(r'"(filial|centro_distribuicao|cidade|bairro|endereco|nome)"', body):
        flag = "  <<< DADOS DE LOJA/FILIAL"
        if lista is None:
            lista = body
    print(f"  [{st}] {u.replace(BASE,'')}{flag}")
    if flag:
        print("       amostra:", body[:700].replace("\n", " "))

pares = []
if lista:
    try:
        d = json.loads(lista)
        blob = d.get("data", d)
        cand = blob if isinstance(blob, list) else \
            (blob.get("filiais") or blob.get("centros_distribuicao") or blob.get("lojas") or [])
        for it in cand if isinstance(cand, list) else []:
            fid = it.get("filial_id") or it.get("filial") or it.get("id")
            cd = it.get("centro_distribuicao_id") or it.get("centro_distribuicao") or 1
            nome = it.get("nome") or it.get("descricao") or it.get("bairro") or it.get("cidade") or "?"
            tipo = it.get("tipo") or it.get("tipo_loja") or it.get("segmento") or ""
            if fid is not None:
                pares.append((str(fid), str(cd), f"{nome} {tipo}".strip()))
                print(f"   -> filial={fid} cd={cd} : {nome} {tipo}")
    except Exception as e:  # noqa: BLE001
        print("   (não parseei auto:", e, ")")


# --------------------------------------------- B) prova: preço por filial
secao("B) MESMA busca 'arroz' em filiais diferentes (preço muda?)")
if not pares:
    pares = [(str(f), str(c), "?") for f in range(1, 9) for c in (1, 2)]

vistos = set()
for fid, cd, nome in pares:
    if (fid, cd) in vistos:
        continue
    vistos.add((fid, cd))
    u = f"{BASE}/org/{ORG}/filial/{fid}/centro_distribuicao/{cd}/loja/buscas/produtos/termo/arroz?page=1"
    st, body = get(u)
    if st and st < 400 and '"produtos"' in body:
        try:
            prods = json.loads(body)["data"]["produtos"]
            amostra = " | ".join(f'{p["descricao"][:24]}={p["preco"]}' for p in prods[:3])
            print(f"  filial={fid} cd={cd} ({nome})  [{st}]  {amostra}")
        except Exception:  # noqa: BLE001
            print(f"  filial={fid} cd={cd} ({nome})  [{st}]  (json inesperado)")
    else:
        print(f"  filial={fid} cd={cd} ({nome})  [{st}]  {body[:70]}")

secao("FIM - cola TODA a saída no chat")
