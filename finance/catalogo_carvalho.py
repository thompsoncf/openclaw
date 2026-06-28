"""finance/catalogo_carvalho.py

Ingestor do catálogo ONLINE do Carvalho Supershop (Grupo Vanguarda) via API
pública da VipCommerce. Lê PREÇO DE REFERÊNCIA (catálogo online) - NÃO substitui
o cupom NFC-e (preço real pago na loja física). Tudo entra rotulado fonte=catalogo.

Estrutura confirmada por reconhecimento:
  base    : https://services.vipcommerce.com.br/api-admin/v1
  org=23  domainkey=carvalhosupershop.com.br  filial=1
  CDs     : 1 (atacarejo, ~229 itens em 'arroz') e 2 (varejo, ~144) -> preço difere por CD
  busca   : .../filial/1/centro_distribuicao/{cd}/loja/buscas/produtos/termo/{termo}?page=N
            ^ ESTE é o endpoint confirmado. A coleta usa ele varrendo itens de mercado.

Token de convidado (Bearer) obrigatório. Configure por env COM O TOKEN REAL:
  export CARVALHO_TOKEN='Bearer eyJ0eXAiOiJKV1Qi...'   (o Bearer INTEIRO do cURL)
  export CARVALHO_SESSION='a2f4128f-...'               (o ?session= da URL; opcional)

Uso (verificar no Render):
  python -m finance.catalogo_carvalho
  -> testa o token, coleta, grava /tmp/catalogo_carvalho.json e imprime resumo +
     comparação de preço por CD pro mesmo GTIN.

NÃO escreve no banco. coletar() devolve registros limpos; a gravação em
precos_observados/banco_preços é o passo seguinte (com Claude Code, após schema).
"""
from __future__ import annotations

import json
import os
import ssl
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://services.vipcommerce.com.br/api-admin/v1"
ORG = os.environ.get("CARVALHO_ORG", "23")
FILIAL = os.environ.get("CARVALHO_FILIAL", "1")
DOMAINKEY = os.environ.get("CARVALHO_DOMAINKEY", "carvalhosupershop.com.br")
CDS = {"1": "Carvalho Supershop (atacarejo)", "2": "Carvalho Supershop (varejo)"}

# Token de convidado CAPTURADO que já funcionou (deu 200 na sonda 4). Temporário:
# some quando expirar. Se isso acontecer, recapture um cURL e exporte CARVALHO_TOKEN.
_TOKEN_EMBUTIDO = ("Bearer eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9."
    "eyJpc3MiOiJ2aXBjb21tZXJjZSIsImF1ZCI6ImFwaS1hZG1pbiIsInN1YiI6IjZiYzQ4NjdlLWRjYTkt"
    "MTFlOS04NzQyLTAyMGQ3OTM1OWNhMCIsInZpcGNvbW1lcmNlQ2xpZW50ZUlkIjpudWxsLCJpYXQiOjE3"
    "ODI2MTM2NDMsInZlciI6MSwiY2xpZW50IjpudWxsLCJvcGVyYXRvciI6bnVsbCwib3JnIjoiMjMifQ."
    "Z9g-iSiCX20vKyBITiDUEhhjDWxiQTkJE8zopvhVc3zC2pUwaMZ9tACLD4tI3s_ow1LXtp467HUxi002_qGzog")
_SESSION_EMBUTIDO = "a2f4128f-1700-4b42-8916-86620e24773d"
_CTX = ssl.create_default_context()


def _placeholder(tok: str) -> bool:
    return (not tok) or ("..." in tok) or (len(tok) < 80) or ("eyJ" not in tok)


# usa o env SÓ se for um token de verdade; senão cai no embutido que funciona
_env_tok = os.environ.get("CARVALHO_TOKEN", "").strip()
_TOKEN = _env_tok if (_env_tok and not _placeholder(_env_tok)) else _TOKEN_EMBUTIDO
_SESSION = os.environ.get("CARVALHO_SESSION", "").strip() or _SESSION_EMBUTIDO

# itens de mercado pra varrer o catálogo pela BUSCA (endpoint confirmado).
# não precisa ser exaustivo: cobre o que importa pro banco de preços (cesta).
TERMOS = [
    "arroz", "feijao", "leite", "acucar", "cafe", "oleo", "macarrao", "farinha",
    "sal", "ovo", "frango", "carne", "linguica", "salsicha", "presunto", "queijo",
    "manteiga", "margarina", "pao", "biscoito", "bolacha", "refrigerante", "suco",
    "agua", "cerveja", "sabao", "detergente", "amaciante", "papel higienico",
    "fralda", "shampoo", "sabonete", "creme dental", "banana", "maca", "tomate",
    "cebola", "batata", "alho", "cenoura", "laranja", "molho de tomate", "extrato",
    "atum", "sardinha", "milho", "ervilha", "achocolatado", "iogurte", "requeijao",
    "mortadela", "tempero", "vinagre", "maionese", "ketchup", "creme de leite",
    "leite condensado", "farofa", "tapioca", "cuscuz",
]


def _headers() -> dict:
    return {
        "accept": "application/json",
        "accept-language": "pt-BR,pt;q=0.9",
        "authorization": _TOKEN,
        "content-type": "application/json",
        "domainkey": DOMAINKEY,
        "organizationid": ORG,
        "origin": f"https://www.{DOMAINKEY}",
        "referer": f"https://www.{DOMAINKEY}/",
        "user-agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/149.0 Safari/537.36"),
    }


def _get(path: str, tentativas: int = 3):
    url = BASE + path
    if _SESSION:
        url += ("&" if "?" in url else "?") + f"session={_SESSION}"
    for i in range(tentativas):
        try:
            req = urllib.request.Request(url, headers=_headers())
            with urllib.request.urlopen(req, timeout=25, context=_CTX) as r:
                return r.status, json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and i < tentativas - 1:
                time.sleep(1.5 * (i + 1)); continue
            try:
                return e.code, json.loads(e.read().decode("utf-8", "replace"))
            except Exception:  # noqa: BLE001
                return e.code, None
        except Exception:  # noqa: BLE001
            if i < tentativas - 1:
                time.sleep(1.0); continue
            return None, None
    return None, None


# ------------------------------------------------------------- utilidades
def _ean_valido(cod: str) -> bool:
    if not cod or not cod.isdigit() or len(cod) not in (8, 12, 13, 14):
        return False
    dig = [int(x) for x in cod]
    soma = sum(v * (3 if i % 2 == 0 else 1) for i, v in enumerate(dig[:-1][::-1]))
    return (10 - (soma % 10)) % 10 == dig[-1]


def _centavos(s):
    try:
        return int(round(float(s) * 100))
    except (TypeError, ValueError):
        return None


def _slug(t: str) -> str:
    t = "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn")
    return urllib.parse.quote(t.strip())


try:
    from finance.categorias import sugerir_categoria  # type: ignore
except Exception:  # noqa: BLE001
    def sugerir_categoria(_n):
        return None


# ------------------------------------------------------------- token check
def verificar_token() -> bool:
    if _placeholder(_TOKEN):
        print("ERRO: CARVALHO_TOKEN não parece o token real.")
        print("  Você provavelmente colou o exemplo 'Bearer eyJ...'.")
        print("  Cole o Bearer INTEIRO do cURL do navegador:")
        print("    export CARVALHO_TOKEN='Bearer eyJ0eXAiOiJKV1Qi................'")
        return False
    pre = f"/org/{ORG}/filial/{FILIAL}/centro_distribuicao/1/loja/buscas/produtos/termo/arroz?page=1"
    st, body = _get(pre)
    if st == 200 and isinstance(body, dict) and body.get("success"):
        n = (body.get("paginator") or {}).get("total_items", "?")
        print(f"  token OK (busca 'arroz' devolveu {n} itens).")
        return True
    print(f"  token NÃO validou: status={st} resp={str(body)[:120]}")
    print("  -> recapture um cURL fresco e refaça o export CARVALHO_TOKEN.")
    return False


# ------------------------------------------------------------- coleta
def _buscar(cd: str, termo: str, page: int):
    pre = f"/org/{ORG}/filial/{FILIAL}/centro_distribuicao/{cd}/loja/buscas/produtos/termo/{_slug(termo)}?page={page}"
    st, body = _get(pre)
    if st == 200 and isinstance(body, dict):
        data = body.get("data") or {}
        prods = data.get("produtos") or []
        tot = (body.get("paginator") or {}).get("total_pages", 1)
        return prods, int(tot or 1)
    return [], 1


def normalizar(p: dict, cd: str) -> dict:
    gtin = str(p.get("codigo_barras") or "").strip()
    oferta = p.get("oferta") or {}
    nome = (p.get("descricao") or "").strip()
    return {
        "fonte": "catalogo", "rede": "carvalho_vanguarda",
        "loja_cd": cd, "loja_nome": CDS.get(cd, f"CD {cd}"),
        "produto_id": p.get("produto_id"), "descricao": nome,
        "categoria": sugerir_categoria(nome), "secao_id": p.get("secao_id"),
        "gtin": gtin if _ean_valido(gtin) else None, "gtin_bruto": gtin or None,
        "sku": p.get("sku"), "codigo_erp": p.get("codigo_erp"),
        "unidade": p.get("unidade_sigla"),
        "preco_centavos": _centavos(p.get("preco")),
        "em_oferta": bool(p.get("em_oferta")),
        "preco_oferta_centavos": _centavos(oferta.get("preco_oferta")) if oferta else None,
        "preco_antigo_centavos": _centavos(oferta.get("preco_antigo")) if oferta else None,
        "atacado_menor_centavos": _centavos(oferta.get("menor_preco")) if oferta else None,
        "disponivel": bool(p.get("disponivel")), "vendidos": p.get("quantidade_vendida"),
        "imagem": p.get("imagem"),  # só o nome do arquivo; URL do CDN: TODO confirmar prefixo
    }


def coletar(cds=None, termos=None, max_paginas=6) -> list[dict]:
    """Varre os CDs pela busca, com PROGRESSO ao vivo (um print por termo) pra
    você ver que não travou. max_paginas baixo = termina rápido (a cesta cabe)."""
    cds = cds or list(CDS)
    termos = termos or TERMOS
    out: dict[tuple, dict] = {}
    for cd in cds:
        print(f"\n  --- CD {cd} ({CDS.get(cd, '')}) ---", flush=True)
        for termo in termos:
            page, total, n = 1, 1, 0
            while page <= min(total, max_paginas):
                prods, total = _buscar(cd, termo, page)
                if not prods:
                    break
                for p in prods:
                    reg = normalizar(p, cd)
                    out[(cd, reg["produto_id"])] = reg
                    n += 1
                page += 1
                time.sleep(0.1)  # gentil com o servidor
            print(f"    {termo:18} +{n}", flush=True)
    return list(out.values())


# ------------------------------------------------------------- standalone
def _resumo(regs: list[dict]) -> None:
    print(f"\n  TOTAL produtos únicos: {len(regs)}")
    com = sum(1 for r in regs if r["gtin"])
    print(f"  com GTIN válido: {com} ({100*com//max(len(regs),1)}%)")
    porg: dict[str, dict[str, int]] = {}
    for r in regs:
        if r["gtin"] and r["preco_centavos"]:
            porg.setdefault(r["gtin"], {})[r["loja_cd"]] = r["preco_centavos"]
    multi = {g: v for g, v in porg.items() if len(v) > 1}
    print(f"  GTINs em mais de 1 CD (dá pra comparar preço): {len(multi)}")
    for g, v in list(multi.items())[:10]:
        nome = next((r["descricao"] for r in regs if r["gtin"] == g), "")
        precos = "  ".join(f"cd{cd}=R${c/100:.2f}" for cd, c in sorted(v.items()))
        print(f"    {g}  {nome[:32]:32} {precos}")


def main() -> None:
    print("Verificando token...")
    if not verificar_token():
        return
    print("Coletando catálogo Carvalho (cd1 + cd2) pela busca...")
    regs = coletar()
    destino = "/tmp/catalogo_carvalho.json"
    with open(destino, "w", encoding="utf-8") as f:
        json.dump(regs, f, ensure_ascii=False, indent=2)
    _resumo(regs)
    print(f"\n  JSON salvo em {destino}")
    print("  (confira e me diga; depois ligamos no banco com o Claude Code.)")


if __name__ == "__main__":
    main()
