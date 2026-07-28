"""Sonda os endpoints de DADOS da Credify pra descobrir o caminho/corpo certos.

O /auth já funciona (ClientID/ClientSecret). O que falta é acertar os endpoints de
quadro societário (CNPJ -> sócios+CPF) e telefone por CPF — o chute atual deu 500.

Rode no Shell do Render (onde a Credify é alcançável):
    export CREDIFY_CLIENT_ID=45547
    export CREDIFY_CLIENT_SECRET=67797495
    python scripts/sonda_credify.py 28276766000112        # CNPJ real
    # opcional: passar um CPF conhecido pra testar o telefone direto:
    # python scripts/sonda_credify.py 28276766000112 12345678901

O que ela faz, em ordem:
  0) tenta baixar um swagger/openapi da API (se existir, LISTA tudo — cole aqui).
  1) testa vários caminhos+corpos do quadro societário; PARA no 1º HTTP 200.
  2) se sair um CPF (ou você passar um), testa o telefone por CPF.

Segurança: MASCARA sequências longas de dígitos (CPF/telefone) na saída — os NOMES
dos campos continuam visíveis, que é o que interessa pra corrigir o parser.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402
from finance import credify as cf  # noqa: E402

BASE = (os.environ.get("CREDIFY_BASE_URL") or "https://api.credify.com.br").rstrip("/")
TIMEOUT = 25.0


def _mask(s: str) -> str:
    """Esconde CPF/telefone: troca runs de 10+ dígitos e CPFs formatados por •••."""
    s = re.sub(r"\d{3}\.\d{3}\.\d{3}-\d{2}", "•••.•••.•••-••", s)
    s = re.sub(r"\d{10,}", lambda m: "•" * len(m.group()), s)
    return s


def _corpo(r: httpx.Response, n: int = 900) -> str:
    t = r.text or ""
    return _mask(t[:n])


def _try(metodo, caminho, *, token=None, json_body=None, params=None):
    url = BASE + caminho
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        r = httpx.request(metodo, url, headers=headers, json=json_body,
                          params=params, timeout=TIMEOUT)
        return r
    except Exception as e:  # noqa: BLE001
        print(f"  {metodo:4} {caminho:34} -> ERRO REDE: {str(e)[:80]}")
        return None


# --------------------------------------------------------------- fase 0: swagger
def fase_swagger():
    print("\n===== [0] procurando swagger/openapi (sem custo) =====")
    candidatos = ["/swagger/v1/swagger.json", "/swagger.json", "/openapi.json",
                  "/v1/swagger.json", "/api-docs", "/swagger/index.html", "/docs/json"]
    achou = False
    for c in candidatos:
        r = _try("GET", c)
        if r is None:
            continue
        marca = "OK" if r.status_code == 200 else str(r.status_code)
        print(f"  GET  {c:34} -> {marca} ({len(r.text)} bytes)")
        if r.status_code == 200 and ("paths" in r.text or "swagger" in r.text.lower()
                                     or "openapi" in r.text.lower()):
            print("\n  >>> SPEC ENCONTRADO! Cole o conteúdo abaixo (só os 'paths' já bastam):")
            try:
                j = r.json()
                print(json.dumps(j.get("paths", j), ensure_ascii=False, indent=2)[:6000])
            except Exception:  # noqa: BLE001
                print(r.text[:6000])
            achou = True
            break
    if not achou:
        print("  (sem swagger público — seguindo pra sondagem por tentativa)")
    return achou


# --------------------------------------------------------------- fase 1: CNPJ
PATHS_CNPJ = ["/pj/quadrosocietario", "/pj/quadro-societario", "/pj/socios", "/pj/qsa",
              "/pj/dadoscadastrais", "/pj/receita", "/pj/cnpj", "/pj/completo",
              "/pj", "/cnpj", "/consulta/pj", "/consulta/quadrosocietario"]
BODYKEYS_CNPJ = ["cnpj", "documento", "doc", "Cnpj", "CNPJ", "Documento"]
# formato "consulta com produto" (muitos agregadores BR usam isso)
PRODUTOS_QS = ["quadrosocietario", "quadro_societario", "QUADRO_SOCIETARIO", "qsa", "socios"]


def _tem_dado(r):
    if r.status_code != 200:
        return False
    t = (r.text or "").lower()
    return any(k in t for k in ("cpf", "socio", "sócio", "nome", "quadro", "dados", "sucess"))


def _extrai_cpf(r):
    try:
        j = r.json()
    except Exception:  # noqa: BLE001
        return None
    txt = json.dumps(j)
    m = re.search(r'"(?:cpf|documento|doc)"\s*:\s*"?(\d{11})"?', txt, re.I)
    return m.group(1) if m else None


def fase_cnpj(token, cnpj):
    print("\n===== [1] sondando QUADRO SOCIETÁRIO (para no 1º HTTP 200) =====")
    cpf_achado = None
    # 1a) caminho + body-key
    for caminho in PATHS_CNPJ:
        for k in BODYKEYS_CNPJ:
            r = _try("POST", caminho, token=token, json_body={k: cnpj})
            if r is None:
                continue
            print(f"  POST {caminho:26} body={{{k}}} -> {r.status_code}"
                  + (f"  {_corpo(r,180)}" if r.text and r.status_code != 500 else ""))
            if _tem_dado(r):
                print(f"\n  >>> ACHOU! POST {caminho} body={{{k}: cnpj}}")
                print("  RESPOSTA (mascarada):\n" + _corpo(r, 3000))
                cpf_achado = _extrai_cpf(r)
                return caminho, k, cpf_achado
    # 1b) formato "produto"
    print("  -- tentando formato {produto, documento} em /consulta e /consultas --")
    for caminho in ["/consulta", "/consultas", "/pj/consulta"]:
        for prod in PRODUTOS_QS:
            r = _try("POST", caminho, token=token,
                     json_body={"produto": prod, "documento": cnpj})
            if r is None:
                continue
            print(f"  POST {caminho:16} produto={prod:20} -> {r.status_code}"
                  + (f"  {_corpo(r,150)}" if r.text and r.status_code != 500 else ""))
            if _tem_dado(r):
                print(f"\n  >>> ACHOU! POST {caminho} produto={prod}")
                print("  RESPOSTA (mascarada):\n" + _corpo(r, 3000))
                return caminho, f"produto={prod}", _extrai_cpf(r)
    # 1c) GET com path/{cnpj} e ?cnpj=
    print("  -- tentando GET path/{cnpj} e ?cnpj= --")
    for caminho in PATHS_CNPJ[:6]:
        for r in (_try("GET", f"{caminho}/{cnpj}", token=token),
                  _try("GET", caminho, token=token, params={"cnpj": cnpj})):
            if r is None:
                continue
            print(f"  GET  {caminho:26} -> {r.status_code}"
                  + (f"  {_corpo(r,150)}" if r.text and r.status_code != 500 else ""))
            if _tem_dado(r):
                print(f"\n  >>> ACHOU (GET)! {caminho}")
                print("  RESPOSTA (mascarada):\n" + _corpo(r, 3000))
                return caminho, "GET", _extrai_cpf(r)
    print("  (nenhum combo do quadro societário respondeu 200)")
    return None, None, cpf_achado


# --------------------------------------------------------------- fase 2: telefone
PATHS_CPF = ["/pf/telefonecpf", "/pf/telefone", "/pf/telefones", "/pf/cpf",
             "/pf/dadoscadastrais", "/pf/contato", "/pf", "/telefone", "/consulta/pf"]
BODYKEYS_CPF = ["cpf", "documento", "doc", "Cpf", "CPF"]


def fase_cpf(token, cpf):
    print("\n===== [2] sondando TELEFONE POR CPF (para no 1º HTTP 200) =====")
    for caminho in PATHS_CPF:
        for k in BODYKEYS_CPF:
            r = _try("POST", caminho, token=token, json_body={k: cpf})
            if r is None:
                continue
            print(f"  POST {caminho:24} body={{{k}}} -> {r.status_code}"
                  + (f"  {_corpo(r,150)}" if r.text and r.status_code != 500 else ""))
            if r.status_code == 200 and any(x in (r.text or "").lower()
                                            for x in ("telefone", "phone", "ddd", "numero", "sucess")):
                print(f"\n  >>> ACHOU! POST {caminho} body={{{k}: cpf}}")
                print("  RESPOSTA (mascarada):\n" + _corpo(r, 3000))
                return caminho, k
    print("  (nenhum combo de telefone respondeu 200)")
    return None, None


def main():
    cnpj = cf._so_digitos(sys.argv[1]) if len(sys.argv) > 1 else "28276766000112"
    cpf_arg = cf._so_digitos(sys.argv[2]) if len(sys.argv) > 2 else None
    print(f"BASE={BASE}  CNPJ={cnpj}  CPF_arg={'sim' if cpf_arg else 'não'}")
    if not cf.tem_credenciais():
        print("FALTAM CREDIFY_CLIENT_ID/SECRET no ambiente."); return
    try:
        token = cf._obter_token()
        print(f"auth OK (token {len(token)} chars)")
    except Exception as e:  # noqa: BLE001
        print(f"auth FALHOU: {e}"); return

    if fase_swagger():
        print("\n>>> Achei o spec acima — cole aqui e eu corrijo tudo de uma vez. "
              "(pulei a sondagem por tentativa)")
        return

    caminho_qs, chave_qs, cpf = fase_cnpj(token, cnpj)
    if caminho_qs:
        print(f"\n[resumo] QUADRO SOCIETÁRIO: POST {caminho_qs}  ({chave_qs})")
    cpf = cpf_arg or cpf
    if cpf:
        cam_tel, chave_tel = fase_cpf(token, cpf)
        if cam_tel:
            print(f"[resumo] TELEFONE: POST {cam_tel}  ({chave_tel})")
    else:
        print("\n[2] pulado: não saiu CPF do quadro societário. Rode de novo passando "
              "um CPF conhecido como 2º argumento pra testar o telefone.")
    print("\nCole TODA esta saída aqui que eu corrijo o finance/credify.py.")


if __name__ == "__main__":
    main()
