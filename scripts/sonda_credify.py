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
# caminho CONFIRMADO na referência (readme): raiz /quadrosocietario. Mantém alguns
# fallbacks só por garantia. O que falta descobrir é o NOME DO CAMPO do corpo.
PATHS_CNPJ = ["/quadrosocietario", "/pj/quadrosocietario"]
BODYKEYS_CNPJ = ["cnpj", "documento", "doc", "Cnpj", "CNPJ", "Documento", "cnpjBusca", "numeroDocumento"]
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


# O endpoint confirmou que o corpo precisa de um "IdConsulta" (o ID do produto/
# consulta contratado). Passe-o por env: CREDIFY_ID_QS (quadro societário) e
# CREDIFY_ID_TEL (telefone). Testamos o campo do documento e o IdConsulta como
# número e como string.
def _ids(valor):
    """Variantes do IdConsulta pra tentar: int e string."""
    out = []
    for v in (valor,):
        try:
            out.append(int(v))
        except (TypeError, ValueError):
            pass
        out.append(str(v))
    return out


def fase_cnpj(token, cnpj, id_consulta):
    print("\n===== [1] QUADRO SOCIETÁRIO =====")
    if not id_consulta:
        r = _try("POST", "/quadrosocietario", token=token, json_body={"cnpj": cnpj})
        print(f"  POST /quadrosocietario body={{cnpj}} -> {r.status_code if r else '—'}"
              + (f"  {_corpo(r,220)}" if r and r.text else ""))
        print("  >>> FALTA o IdConsulta. Descubra o ID da consulta 'Quadro Societário' "
              "e rode com:  export CREDIFY_ID_QS=<id>")
        return None, None, None
    for id_v in _ids(id_consulta):
        for k in BODYKEYS_CNPJ:
            body = {"IdConsulta": id_v, k: cnpj}
            r = _try("POST", "/quadrosocietario", token=token, json_body=body)
            if r is None:
                continue
            print(f"  POST /quadrosocietario IdConsulta={id_v!r:>16} doc={{{k}}} -> {r.status_code}"
                  + (f"  {_corpo(r,200)}" if r.text else ""))
            if _tem_dado(r):
                print(f"\n  >>> ACHOU! body={{IdConsulta:{id_v!r}, {k}:cnpj}}")
                print("  RESPOSTA (mascarada):\n" + _corpo(r, 3500))
                return k, id_v, _extrai_cpf(r)
    print("  (nenhum combo com esse IdConsulta trouxe dados — confira o ID)")
    return None, None, None


# --------------------------------------------------------------- fase 2: telefone
PATHS_CPF = ["/pftelefonecpf"]
BODYKEYS_CPF = ["cpf", "documento", "doc", "Cpf", "CPF", "numeroDocumento"]


def fase_cpf(token, cpf, id_consulta):
    print("\n===== [2] TELEFONE POR CPF =====")
    if not id_consulta:
        r = _try("POST", "/pftelefonecpf", token=token, json_body={"cpf": cpf})
        print(f"  POST /pftelefonecpf body={{cpf}} -> {r.status_code if r else '—'}"
              + (f"  {_corpo(r,220)}" if r and r.text else ""))
        print("  >>> FALTA o IdConsulta. Descubra o ID da consulta 'Telefone por CPF' "
              "e rode com:  export CREDIFY_ID_TEL=<id>")
        return None, None
    for id_v in _ids(id_consulta):
        for k in BODYKEYS_CPF:
            body = {"IdConsulta": id_v, k: cpf}
            r = _try("POST", "/pftelefonecpf", token=token, json_body=body)
            if r is None:
                continue
            print(f"  POST /pftelefonecpf IdConsulta={id_v!r:>16} doc={{{k}}} -> {r.status_code}"
                  + (f"  {_corpo(r,200)}" if r.text else ""))
            if r.status_code == 200 and any(x in (r.text or "").lower()
                                            for x in ("telefone", "phone", "ddd", "numero", "sucess")):
                print(f"\n  >>> ACHOU! body={{IdConsulta:{id_v!r}, {k}:cpf}}")
                print("  RESPOSTA (mascarada):\n" + _corpo(r, 3500))
                return k, id_v
    print("  (nenhum combo com esse IdConsulta trouxe telefone — confira o ID)")
    return None, None


def main():
    cnpj = cf._so_digitos(sys.argv[1]) if len(sys.argv) > 1 else "28276766000112"
    cpf_arg = cf._so_digitos(sys.argv[2]) if len(sys.argv) > 2 else None
    id_qs = os.environ.get("CREDIFY_ID_QS")
    id_tel = os.environ.get("CREDIFY_ID_TEL")
    print(f"BASE={BASE}  CNPJ={cnpj}  ID_QS={id_qs or '(não setado)'}  ID_TEL={id_tel or '(não setado)'}")
    if not cf.tem_credenciais():
        print("FALTAM CREDIFY_CLIENT_ID/SECRET no ambiente."); return
    try:
        token = cf._obter_token()
        print(f"auth OK (token {len(token)} chars)")
    except Exception as e:  # noqa: BLE001
        print(f"auth FALHOU: {e}"); return

    _k, _id, cpf = fase_cnpj(token, cnpj, id_qs)
    cpf = cpf_arg or cpf
    if id_tel and cpf:
        fase_cpf(token, cpf, id_tel)
    elif id_tel and not cpf:
        print("\n[2] pulado: não saiu CPF do quadro societário. Rode passando um CPF "
              "como 2º argumento:  python scripts/sonda_credify.py <CNPJ> <CPF>")
    else:
        fase_cpf(token, cpf or "00000000000", id_tel)   # mostra o erro/pedido de IdConsulta
    print("\nCole TODA esta saída aqui.")


if __name__ == "__main__":
    main()
