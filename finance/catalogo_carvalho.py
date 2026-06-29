"""finance/catalogo_carvalho.py

Ingestor GENÉRICO de catálogo VipCommerce (multi-rede). Lê PREÇO DE REFERÊNCIA
(catálogo online) - NÃO substitui o cupom NFC-e (preço real por loja). Tudo entra
rotulado fonte='catalogo' via BancoPrecos.registrar_catalogo.

Cada rede VipCommerce é só CONFIG (org, domainkey, filial, CDs, prefixo de env).
A lógica (token, busca, normalização, GTIN) é a mesma pra todas.

  ATENÇÃO: isto vale pra VipCommerce. Ferreira (ferreiraemcasa) e Mateus (mateusmais)
  rodam plataforma PRÓPRIA (custom) -> precisam de adaptador separado, não entram aqui.

Token/sessão por rede via env <PREFIXO>_TOKEN / <PREFIXO>_SESSION:
  carvalho    -> CARVALHO_TOKEN  / CARVALHO_SESSION
  r_carvalho  -> RCARVALHO_TOKEN / RCARVALHO_SESSION

Uso (Render):
  python -m finance.catalogo_carvalho                    # rede padrão (carvalho), só JSON
  python -m finance.catalogo_carvalho carvalho --gravar  # coleta e grava no banco
  python -m finance.catalogo_carvalho r_carvalho         # depois de preencher org/CDs
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://services.vipcommerce.com.br/api-admin/v1"

# token de convidado do Carvalho que JÁ funcionou (temporário; some quando expirar)
_CARVALHO_TOKEN_FALLBACK = ("Bearer eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9."
    "eyJpc3MiOiJ2aXBjb21tZXJjZSIsImF1ZCI6ImFwaS1hZG1pbiIsInN1YiI6IjZiYzQ4NjdlLWRjYTkt"
    "MTFlOS04NzQyLTAyMGQ3OTM1OWNhMCIsInZpcGNvbW1lcmNlQ2xpZW50ZUlkIjpudWxsLCJpYXQiOjE3"
    "ODI2MTM2NDMsInZlciI6MSwiY2xpZW50IjpudWxsLCJvcGVyYXRvciI6bnVsbCwib3JnIjoiMjMifQ."
    "Z9g-iSiCX20vKyBITiDUEhhjDWxiQTkJE8zopvhVc3zC2pUwaMZ9tACLD4tI3s_ow1LXtp467HUxi002_qGzog")

# ----------------------------------------------------------------- REDES (config)
REDES: dict[str, dict] = {
    "carvalho": {
        "rede": "carvalho_vanguarda",
        "nome": "Carvalho Supershop (Grupo Vanguarda)",
        "org": "23",
        "domainkey": "carvalhosupershop.com.br",
        "filial": "1",
        "cds": {"1": "Carvalho Supershop (atacarejo)",
                "2": "Carvalho Supershop (varejo)"},
        "regiao": "Teresina",
        "env": "CARVALHO",
        "token_fallback": _CARVALHO_TOKEN_FALLBACK,
        "session_fallback": "a2f4128f-1700-4b42-8916-86620e24773d",
    },
    "ferreira": {
        "rede": "ferreira_em_casa",
        "nome": "Ferreira em Casa",
        "org": "123",
        "domainkey": "ferreiraemcasa.com.br",
        "filial": "1",
        "cds": {"1": "Ferreira em Casa"},
        "regiao": "Teresina",
        "env": "FERREIRA",
        "token_fallback": ("Bearer eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9."
            "eyJpc3MiOiJ2aXBjb21tZXJjZSIsImF1ZCI6ImFwaS1hZG1pbiIsInN1YiI6IjZiYzQ4NjdlLWRjYTkt"
            "MTFlOS04NzQyLTAyMGQ3OTM1OWNhMCIsInZpcGNvbW1lcmNlQ2xpZW50ZUlkIjpudWxsLCJpYXQiOjE3ODI3NDM3OTksInZlciI6MSwiY2xpZW50IjpudWxsLCJvcGVyYXRvciI6bnVsbCwib3JnIjoiMTIzIn0."
            "FY7QDSO3boNUdIXBJVHW4_7GRS4HWa-UeCQm7v5i37WkPKQFP75l8do6MzazBL7dqk5aPu9ymR83aopjHPFTrA"),
        "session_fallback": "20dd9781-25a4-43e4-b991-bda1fb2978cf",
    },
    # Esqueleto: preencher 'org' e 'cds' quando capturar o cURL do gruporcarvalho.com.br
    # (F12 > Network > busca 'arroz' > Copy as cURL) e exportar RCARVALHO_TOKEN/SESSION.
    "r_carvalho": {
        "rede": "r_carvalho",
        "nome": "R Carvalho (Grupo R Carvalho / Reginaldo)",
        "org": None,
        "domainkey": "gruporcarvalho.com.br",
        "filial": "1",
        "cds": {},                 # ex.: {"1": "R Carvalho (atacarejo)", "2": "R Carvalho (varejo)"}
        "regiao": "Teresina",
        "env": "RCARVALHO",
        "token_fallback": "",
        "session_fallback": "",
    },
}

# itens de mercado pra varrer o catálogo pela BUSCA (endpoint confirmado).
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

_CTX = ssl.create_default_context()


# ----------------------------------------------------------------- credenciais
def _placeholder(tok: str) -> bool:
    return (not tok) or ("..." in tok) or (len(tok) < 80) or ("eyJ" not in tok)


def _token(rede: dict) -> str:
    env = rede.get("env", "")
    tok = os.environ.get(f"{env}_TOKEN", "").strip()
    if tok and not _placeholder(tok):
        return tok
    return rede.get("token_fallback", "")


def _session(rede: dict) -> str:
    env = rede.get("env", "")
    return os.environ.get(f"{env}_SESSION", "").strip() or rede.get("session_fallback", "")


def _headers(rede: dict) -> dict:
    return {
        "accept": "application/json",
        "accept-language": "pt-BR,pt;q=0.9",
        "authorization": _token(rede),
        "content-type": "application/json",
        "domainkey": rede["domainkey"],
        "organizationid": str(rede["org"]),
        "origin": f"https://www.{rede['domainkey']}",
        "referer": f"https://www.{rede['domainkey']}/",
        "user-agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/149.0 Safari/537.36"),
    }


def _get(rede: dict, path: str, tentativas: int = 3):
    url = BASE + path
    sess = _session(rede)
    if sess:
        url += ("&" if "?" in url else "?") + f"session={sess}"
    for i in range(tentativas):
        try:
            req = urllib.request.Request(url, headers=_headers(rede))
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
def verificar_token(rede: dict) -> bool:
    if rede.get("org") in (None, "", 0) or not rede.get("cds"):
        print(f"CONFIG INCOMPLETA pra '{rede['rede']}': falta 'org' e/ou 'cds'.")
        print("  Capture o cURL da busca no site da rede, descubra org + CDs e preencha em REDES.")
        return False
    if _placeholder(_token(rede)):
        print(f"ERRO: token de '{rede['rede']}' ausente/placeholder.")
        print(f"  export {rede['env']}_TOKEN='Bearer eyJ...'  (o Bearer inteiro do cURL)")
        return False
    cd0 = next(iter(rede["cds"]))
    pre = (f"/org/{rede['org']}/filial/{rede['filial']}/centro_distribuicao/{cd0}"
           f"/loja/buscas/produtos/termo/arroz?page=1")
    st, body = _get(rede, pre)
    if st == 200 and isinstance(body, dict) and body.get("success"):
        n = (body.get("paginator") or {}).get("total_items", "?")
        print(f"  token OK ({rede['rede']}: busca 'arroz' devolveu {n} itens).")
        return True
    print(f"  token NÃO validou ({rede['rede']}): status={st} resp={str(body)[:120]}")
    return False


# ------------------------------------------------------------- coleta
def _buscar(rede: dict, cd: str, termo: str, page: int):
    pre = (f"/org/{rede['org']}/filial/{rede['filial']}/centro_distribuicao/{cd}"
           f"/loja/buscas/produtos/termo/{_slug(termo)}?page={page}")
    st, body = _get(rede, pre)
    if st == 200 and isinstance(body, dict):
        data = body.get("data") or {}
        prods = data.get("produtos") or []
        tot = (body.get("paginator") or {}).get("total_pages", 1)
        return prods, int(tot or 1)
    return [], 1


def normalizar(p: dict, cd: str, rede: dict) -> dict:
    gtin = str(p.get("codigo_barras") or "").strip()
    oferta = p.get("oferta") or {}
    nome = (p.get("descricao") or "").strip()
    return {
        "fonte": "catalogo", "rede": rede["rede"],
        "loja_cd": cd, "loja_nome": rede["cds"].get(cd, f"{rede['rede']} CD{cd}"),
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
        "imagem": p.get("imagem"),
    }


def coletar(rede: dict, termos=None, max_paginas=6) -> list[dict]:
    termos = termos or TERMOS
    out: dict[tuple, dict] = {}
    for cd in rede["cds"]:
        print(f"\n  --- {rede['rede']} CD {cd} ({rede['cds'][cd]}) ---", flush=True)
        for termo in termos:
            page, total, n = 1, 1, 0
            while page <= min(total, max_paginas):
                prods, total = _buscar(rede, cd, termo, page)
                if not prods:
                    break
                for p in prods:
                    reg = normalizar(p, cd, rede)
                    out[(cd, reg["produto_id"])] = reg
                    n += 1
                page += 1
                time.sleep(0.1)
            print(f"    {termo:18} +{n}", flush=True)
    return list(out.values())


# ------------------------------------------------------------- gravação
def gravar_no_banco(regs: list[dict], regiao: str = "Teresina") -> int:
    """Grava em precos_observados com fonte='catalogo', via pool oficial do
    projeto (db.conexao.get_pool, psycopg3) e BancoPrecos.registrar_catalogo
    (idempotente, sem migração). Precisa de DATABASE_URL e do método
    registrar_catalogo já inserido no finance/banco_precos.py."""
    from db.conexao import get_pool
    from finance.banco_precos import BancoPrecos
    n = BancoPrecos(get_pool()).registrar_catalogo(regs, regiao=regiao)
    print(f"  gravados/atualizados: {n} (fonte='catalogo', regiao={regiao})")
    return n


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
    print(f"  GTINs em mais de 1 CD (compara preço): {len(multi)}")
    for g, v in list(multi.items())[:8]:
        nome = next((r["descricao"] for r in regs if r["gtin"] == g), "")
        precos = "  ".join(f"cd{cd}=R${c/100:.2f}" for cd, c in sorted(v.items()))
        print(f"    {g}  {nome[:30]:30} {precos}")


def main() -> None:
    args = [a for a in sys.argv[1:]]
    slug = next((a for a in args if not a.startswith("-")), "carvalho")
    gravar = "--gravar" in args
    rede = REDES.get(slug)
    if not rede:
        print(f"Rede '{slug}' não existe. Disponíveis: {', '.join(REDES)}")
        return
    print(f"Rede: {rede['nome']}  (slug={slug})")
    print("Verificando token...")
    if not verificar_token(rede):
        return
    print(f"Coletando catálogo de {rede['rede']} pela busca...")
    regs = coletar(rede)
    destino = f"/tmp/catalogo_{slug}.json"
    with open(destino, "w", encoding="utf-8") as f:
        json.dump(regs, f, ensure_ascii=False, indent=2)
    _resumo(regs)
    print(f"\n  JSON salvo em {destino}")
    if gravar:
        print("\nGravando no banco de preços...")
        gravar_no_banco(regs, regiao=rede["regiao"])
    else:
        print("  (rode com --gravar pra escrever no banco; sem isso, só gera o JSON.)")


if __name__ == "__main__":
    main()
