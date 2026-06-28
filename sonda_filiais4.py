#!/usr/bin/env python3
"""Sonda v4 - enumera os CDs (centros de distribuição) do Carvalho e acha nomes.

Já sabemos: filial=1 é a única; o preço muda por CD. Aqui varremos cd=1..15
sob filial 1 (e tentamos filial 0/2 por garantia), mostrando quais existem, uma
amostra de preço, e se tem 'oferta atacarejo' (pra inferir atacarejo vs varejo).
Também caçamos um endpoint que liste o NOME de cada CD.

Roda:  python sonda_filiais4.py   -> cola a saída.
Reaproveita as credenciais embutidas (token validado na v3). Só lê.
"""
import base64
import json
import ssl
import urllib.request
import urllib.error

ORG = "23"
SESSION = "a2f4128f-1700-4b42-8916-86620e24773d"
AUTHORIZATION = "Bearer eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJpc3MiOiJ2aXBjb21tZXJjZSIsImF1ZCI6ImFwaS1hZG1pbiIsInN1YiI6IjZiYzQ4NjdlLWRjYTktMTFlOS04NzQyLTAyMGQ3OTM1OWNhMCIsInZpcGNvbW1lcmNlQ2xpZW50ZUlkIjpudWxsLCJpYXQiOjE3ODI2MTM2NDMsInZlciI6MSwiY2xpZW50IjpudWxsLCJvcGVyYXRvciI6bnVsbCwib3JnIjoiMjMifQ.Z9g-iSiCX20vKyBITiDUEhhjDWxiQTkJE8zopvhVc3zC2pUwaMZ9tACLD4tI3s_ow1LXtp467HUxi002_qGzog"

BASE = "https://services.vipcommerce.com.br/api-admin/v1"
HEADERS = {
    "accept": "application/json",
    "accept-language": "pt-BR,pt;q=0.9",
    "authorization": AUTHORIZATION,
    "content-type": "application/json",
    "domainkey": "carvalhosupershop.com.br",
    "organizationid": ORG,
    "origin": "https://www.carvalhosupershop.com.br",
    "referer": "https://www.carvalhosupershop.com.br/",
    "sessao-id": "60be7ce826dc93b0ff32c8e43fb585d8",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/149.0 Safari/537.36",
}
_CTX = ssl.create_default_context()


def get(url, timeout=20):
    full = url + ("&" if "?" in url else "?") + "session=" + SESSION
    try:
        req = urllib.request.Request(full, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
            return r.status, r.read(800_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read(500).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            return e.code, ""
    except Exception as e:  # noqa: BLE001
        return None, f"ERRO: {e}"


def secao(t):
    print("\n" + "=" * 70 + f"\n{t}\n" + "=" * 70)


# --------------------------------------- A) enumera CDs vivos (filial x cd)
secao("A) ENUMERA CDs vivos - busca 'arroz' por (filial, cd)")
vivos = []
dump_keys_feito = False
for fil in (0, 1, 2):
    for cd in range(0, 16):
        u = f"{BASE}/org/{ORG}/filial/{fil}/centro_distribuicao/{cd}/loja/buscas/produtos/termo/arroz?page=1"
        st, body = get(u)
        if st == 200 and '"produtos"' in body:
            try:
                data = json.loads(body)["data"]
                prods = data["produtos"]
                total = json.loads(body).get("paginator", {}).get("total_items", "?")
                p0 = prods[0] if prods else {}
                oferta = (p0.get("oferta") or {}).get("nome", "")
                amostra = " | ".join(f'{p["descricao"][:20]}={p["preco"]}' for p in prods[:2])
                tag = " [ATACAREJO]" if "atacarejo" in (oferta or "").lower() else ""
                print(f"  VIVO filial={fil} cd={cd}  total={total}{tag}  {amostra}")
                vivos.append((fil, cd))
                if not dump_keys_feito:
                    outras = {k: (str(v)[:60]) for k, v in data.items() if k != "produtos"}
                    print("       (chaves extras na resposta:", outras, ")")
                    dump_keys_feito = True
            except Exception as e:  # noqa: BLE001
                print(f"  VIVO filial={fil} cd={cd}  (json inesperado: {e})")
        elif st not in (404, None):
            print(f"  [{st}] filial={fil} cd={cd}  {body[:60]}")
print(f"\n  -> CDs vivos: {vivos}")


# --------------------------------------- B) caça o nome/endereço dos CDs
secao("B) CAÇA por listagem com NOME/endereço dos CDs")
ALVOS = [
    f"{BASE}/org/{ORG}/filial/1/centros_distribuicao",
    f"{BASE}/org/{ORG}/filial/1/centro_distribuicao",
    f"{BASE}/org/{ORG}/loja/filial/1/centros_distribuicao",
    f"{BASE}/org/{ORG}/filial/1/centro_distribuicao/1/loja/centros_distribuicao",
    f"{BASE}/org/{ORG}/filial/1/centro_distribuicao/1/centros_distribuicao",
    f"{BASE}/org/{ORG}/estabelecimentos",
    f"{BASE}/org/{ORG}/lojas",
    f"{BASE}/org/{ORG}/filiais/1/centros_distribuicao",
    f"{BASE}/org/{ORG}/filial/1/centro_distribuicao/1/loja/parametros",
    f"{BASE}/org/{ORG}/filial/1/centro_distribuicao/1/loja/configuracoes",
]
for u in ALVOS:
    st, body = get(u)
    eh_json = body[:1].strip() in "[{"
    print(f"  [{st}] {u.replace(BASE,'')}")
    if st == 200 and eh_json:
        print("       amostra:", body[:800].replace("\n", " "))

secao("FIM - cola TODA a saída")
