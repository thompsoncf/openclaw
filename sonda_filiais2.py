#!/usr/bin/env python3
"""Sonda de FILIAIS v2 - lê o cURL de um arquivo (NADA é retipado).

Por que v2: na v1 eu (assistente) reescrevi o Bearer e corrompí -> 401 "error
while decoding token". Aqui o script lê o teu cURL verbatim de 'curl.txt', extrai
URL + headers exatos, e só troca a filial/CD pra provar que o preço muda.

COMO USAR no Render:
  1) cria o arquivo colando teu cURL (o mesmo que você me mandou):
       cat > curl.txt << 'EOF'
       curl 'https://services.vipcommerce.com.br/api-admin/v1/org/23/...' \
         -H 'authorization: Bearer ...' \
         -H 'domainkey: carvalhosupershop.com.br' \
         ... (cola TODAS as linhas) ...
       EOF
  2) python sonda_filiais2.py
  3) cola TODA a saída aqui.

Se der 401 de novo: o token expirou no tempo - recaptura um cURL novo e refaz o
curl.txt. Só lê. Stdlib pura.
"""
import json
import re
import ssl
import urllib.request
import urllib.error

_CTX = ssl.create_default_context()


def parse_curl(caminho="curl.txt"):
    with open(caminho, encoding="utf-8") as f:
        txt = f.read()
    # URL = 1ª string http(s) entre aspas (simples ou duplas)
    m = re.search(r"""['"](https?://[^'"]+)['"]""", txt)
    url = m.group(1) if m else None
    headers = {}
    for k, v in re.findall(r"-H\s+'([^:]+):\s*([^']*)'", txt):
        headers[k.strip().lower()] = v.strip()
    for k, v in re.findall(r'-H\s+"([^:]+):\s*([^"]*)"', txt):
        headers[k.strip().lower()] = v.strip()
    return url, headers


def get(url, headers, timeout=20):
    try:
        req = urllib.request.Request(url, headers=headers)
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


def secao(t):
    print("\n" + "=" * 70 + f"\n{t}\n" + "=" * 70)


# ---------------------------------------------------------------- parse cURL
secao("PARSE - lendo curl.txt")
try:
    URL, HEADERS = parse_curl()
except FileNotFoundError:
    print("  !! curl.txt não encontrado. Crie com 'cat > curl.txt << EOF' (ver topo).")
    raise SystemExit(1)

print("  URL capturada:", URL)
print("  headers lidos:", ", ".join(sorted(HEADERS)))
auth_ok = "authorization" in HEADERS and len(HEADERS["authorization"]) > 40
print("  authorization presente:", auth_ok, f"({len(HEADERS.get('authorization',''))} chars)")

# extrai base, org, filial, cd, session
m = re.search(r"(https?://[^/]+/api-admin/v\d+)/org/(\d+)/filial/(\d+)/centro_distribuicao/(\d+)", URL)
if not m:
    print("  !! não reconheci o padrão /org/N/filial/N/centro_distribuicao/N na URL.")
    print("     Ajuste manualmente ou recapture o cURL da BUSCA de produtos.")
    raise SystemExit(1)
BASE, ORG, FIL0, CD0 = m.group(1), m.group(2), m.group(3), m.group(4)
ms = re.search(r"[?&]session=([0-9a-f\-]+)", URL)
SESSION = ms.group(1) if ms else ""
print(f"  base={BASE}  org={ORG}  filial_atual={FIL0}  cd_atual={CD0}  session={'sim' if SESSION else 'não'}")


def comsession(u):
    if not SESSION:
        return u
    return u + ("&" if "?" in u else "?") + "session=" + SESSION


# --------------------------------------------- PARTE A: descobrir filiais (com token bom)
secao("PARTE A - listar filiais/CD (agora COM token válido)")
CANDIDATOS = [
    f"{BASE}/org/{ORG}/loja",                       # deu 401 antes => existe
    f"{BASE}/org/{ORG}/loja/filiais",
    f"{BASE}/org/{ORG}/filiais",
    f"{BASE}/org/{ORG}/loja/centros_distribuicao",
    f"{BASE}/org/{ORG}/centros_distribuicao",
    f"{BASE}/org/{ORG}/loja/dados",
    f"{BASE}/org/{ORG}/loja/lojas",
    f"{BASE}/org/{ORG}/filial/{FIL0}/centro_distribuicao/{CD0}/loja",
    f"{BASE}/org/{ORG}/filial/{FIL0}/centro_distribuicao/{CD0}/loja/filiais",
    f"{BASE}/org/{ORG}/filial/{FIL0}/centro_distribuicao/{CD0}/loja/enderecos",
]
lista_filiais = None
for u in CANDIDATOS:
    st, body = get(comsession(u), HEADERS)
    eh_json = body[:1].strip() in "[{"
    flag = ""
    if st and st < 400 and eh_json and re.search(r'"(filial|centro_distribuicao|cidade|bairro|endereco|nome)"', body):
        flag = "  <<< DADOS DE LOJA/FILIAL"
        if lista_filiais is None:
            lista_filiais = body
    print(f"  [{st}] {u.replace(BASE,'')}{flag}")
    if flag:
        print("       amostra:", body[:600].replace("\n", " "))


# --------------------------------------- PARTE B: PROVA preço por filial
secao("PARTE B - mesma busca 'arroz' em filiais diferentes (preço muda?)")
# tenta extrair pares (filial,cd) reais da listagem; senão usa grade
pares = []
if lista_filiais:
    try:
        d = json.loads(lista_filiais)
        blob = d.get("data", d)
        cand = blob if isinstance(blob, list) else \
            (blob.get("filiais") or blob.get("centros_distribuicao") or blob.get("lojas") or [])
        for it in cand if isinstance(cand, list) else []:
            fid = it.get("filial_id") or it.get("filial") or it.get("id")
            cd = it.get("centro_distribuicao_id") or it.get("centro_distribuicao") or 1
            nome = it.get("nome") or it.get("descricao") or it.get("bairro") or it.get("cidade") or "?"
            if fid is not None:
                pares.append((str(fid), str(cd), nome))
    except Exception:  # noqa: BLE001
        pass
if not pares:
    pares = [(str(f), str(c), "?") for f in (1, 2, 3, 4, 5) for c in (1, 2)]

vistos = set()
for fid, cd, nome in pares:
    if (fid, cd) in vistos:
        continue
    vistos.add((fid, cd))
    u = f"{BASE}/org/{ORG}/filial/{fid}/centro_distribuicao/{cd}/loja/buscas/produtos/termo/arroz?page=1"
    st, body = get(comsession(u), HEADERS)
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
