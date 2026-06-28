"""finance/catalogo_carvalho.py

Ingestor do catálogo ONLINE do Carvalho Supershop (Grupo Vanguarda) via API
pública da VipCommerce. Lê PREÇO DE REFERÊNCIA (catálogo online) - NÃO substitui
o cupom NFC-e (preço real pago na loja física). Tudo entra rotulado fonte=catalogo.

Estrutura confirmada por reconhecimento:
  base    : https://services.vipcommerce.com.br/api-admin/v1
  org=23  domainkey=carvalhosupershop.com.br  filial=1
  CDs     : 1 (atacarejo, ~229 itens em 'arroz') e 2 (varejo, ~144)  -> preço difere por CD
  busca   : .../filial/1/centro_distribuicao/{cd}/loja/buscas/produtos/termo/{termo}
  catálogo: .../loja/departamentos   e   .../loja/produtos/departamento/{dep}  (paginado)

Token de convidado (Bearer) é obrigatório. Configure por env:
  export CARVALHO_TOKEN='Bearer eyJ...'      (o cURL do navegador)
  export CARVALHO_SESSION='a2f4128f-...'     (o ?session= da URL; opcional)
TODO(cron): achar a rota de emissão do token (não estava nos 3 bundles da home;
provavelmente num chunk lazy). Por ora o token é setado na mão via env.

Uso standalone (verificar no Render):
  python -m finance.catalogo_carvalho
  -> grava /tmp/catalogo_carvalho.json e imprime um resumo + comparação de preço por CD.

Este módulo NÃO escreve no banco. A função coletar() devolve registros limpos;
a gravação em precos_observados/banco_preços é o passo seguinte (com Claude Code,
após confirmar o schema).
"""
from __future__ import annotations

import json
import os
import ssl
import time
import urllib.request
import urllib.error

BASE = "https://services.vipcommerce.com.br/api-admin/v1"
ORG = os.environ.get("CARVALHO_ORG", "23")
FILIAL = os.environ.get("CARVALHO_FILIAL", "1")
DOMAINKEY = os.environ.get("CARVALHO_DOMAINKEY", "carvalhosupershop.com.br")
# CD -> rótulo da loja (provisório; cd1 trazia "Oferta atacarejo")
CDS = {"1": "Carvalho Supershop (atacarejo)", "2": "Carvalho Supershop (varejo)"}

_TOKEN = os.environ.get("CARVALHO_TOKEN", "")     # 'Bearer eyJ...'
_SESSION = os.environ.get("CARVALHO_SESSION", "")
_CTX = ssl.create_default_context()


def _headers() -> dict:
    if not _TOKEN:
        raise RuntimeError("Defina CARVALHO_TOKEN no ambiente (ex.: export "
                           "CARVALHO_TOKEN='Bearer eyJ...').")
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


def _get(path: str, tentativas: int = 3) -> tuple[int | None, dict | list | None]:
    url = BASE + path
    url += ("&" if "?" in url else "?") + f"session={_SESSION}" if _SESSION else ""
    for i in range(tentativas):
        try:
            req = urllib.request.Request(url, headers=_headers())
            with urllib.request.urlopen(req, timeout=25, context=_CTX) as r:
                return r.status, json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and i < tentativas - 1:
                time.sleep(1.5 * (i + 1))
                continue
            try:
                return e.code, json.loads(e.read().decode("utf-8", "replace"))
            except Exception:  # noqa: BLE001
                return e.code, None
        except Exception:  # noqa: BLE001
            if i < tentativas - 1:
                time.sleep(1.0)
                continue
            return None, None
    return None, None


# ----------------------------------------------------------------- utilidades
def _ean_valido(cod: str) -> bool:
    """Valida GTIN/EAN (8/12/13/14 dígitos) pelo dígito verificador GS1 mod-10."""
    if not cod or not cod.isdigit() or len(cod) not in (8, 12, 13, 14):
        return False
    dig = [int(x) for x in cod]
    chk = dig[-1]
    corpo = dig[:-1][::-1]
    soma = sum(v * (3 if i % 2 == 0 else 1) for i, v in enumerate(corpo))
    return (10 - (soma % 10)) % 10 == chk


def _centavos(s) -> int | None:
    try:
        return int(round(float(s) * 100))
    except (TypeError, ValueError):
        return None


try:
    from finance.categorias import sugerir_categoria  # type: ignore
except Exception:  # noqa: BLE001
    def sugerir_categoria(_nome: str):  # fallback
        return None


# --------------------------------------------------------------- chamadas API
def listar_departamentos(cd: str) -> list[dict]:
    pre = f"/org/{ORG}/filial/{FILIAL}/centro_distribuicao/{cd}/loja"
    st, body = _get(f"{pre}/departamentos")
    if st == 200 and isinstance(body, dict):
        data = body.get("data", body)
        return data if isinstance(data, list) else (data.get("departamentos") or [])
    print(f"  [warn] departamentos cd={cd} -> status {st}")
    return []


def _produtos_pagina(cd: str, dep_id, page: int) -> tuple[list[dict], int]:
    """Tenta as variantes de rota de produtos-por-departamento. Devolve
    (produtos, total_pages). Memoriza a 1ª variante que funcionar."""
    pre = f"/org/{ORG}/filial/{FILIAL}/centro_distribuicao/{cd}/loja"
    variantes = [
        f"{pre}/produtos/departamento/{dep_id}?page={page}",
        f"{pre}/departamentos/{dep_id}/produtos?page={page}",
        f"{pre}/produtos/departamentos/{dep_id}?page={page}",
    ]
    for v in variantes:
        st, body = _get(v)
        if st == 200 and isinstance(body, dict):
            data = body.get("data", {})
            prods = data.get("produtos") if isinstance(data, dict) else (data if isinstance(data, list) else [])
            if prods is not None:
                tot = (body.get("paginator") or {}).get("total_pages", 1)
                return prods, int(tot or 1)
    return [], 1


def normalizar(p: dict, cd: str) -> dict:
    gtin = str(p.get("codigo_barras") or "").strip()
    oferta = p.get("oferta") or {}
    nome = (p.get("descricao") or "").strip()
    return {
        "fonte": "catalogo",
        "rede": "carvalho_vanguarda",
        "loja_cd": cd,
        "loja_nome": CDS.get(cd, f"CD {cd}"),
        "produto_id": p.get("produto_id"),
        "descricao": nome,
        "categoria": sugerir_categoria(nome),
        "secao_id": p.get("secao_id"),
        "gtin": gtin if _ean_valido(gtin) else None,
        "gtin_bruto": gtin or None,
        "sku": p.get("sku"),
        "codigo_erp": p.get("codigo_erp"),
        "unidade": p.get("unidade_sigla"),
        "preco_centavos": _centavos(p.get("preco")),
        "em_oferta": bool(p.get("em_oferta")),
        "preco_oferta_centavos": _centavos(oferta.get("preco_oferta")) if oferta else None,
        "preco_antigo_centavos": _centavos(oferta.get("preco_antigo")) if oferta else None,
        "atacado_menor_centavos": _centavos(oferta.get("menor_preco")) if oferta else None,
        "disponivel": bool(p.get("disponivel")),
        "vendidos": p.get("quantidade_vendida"),
        "imagem": p.get("imagem"),   # só o nome do arquivo; URL do CDN: TODO confirmar prefixo
    }


def coletar(cds: list[str] | None = None, limite_por_dep: int = 50) -> list[dict]:
    """Varre os CDs e seus departamentos, devolve registros normalizados e
    deduplicados por (cd, produto_id)."""
    cds = cds or list(CDS)
    out: dict[tuple, dict] = {}
    for cd in cds:
        deps = listar_departamentos(cd)
        print(f"  cd={cd}: {len(deps)} departamentos")
        for dep in deps:
            dep_id = dep.get("id") or dep.get("departamento_id") or dep.get("link")
            page, total = 1, 1
            while page <= min(total, limite_por_dep):
                prods, total = _produtos_pagina(cd, dep_id, page)
                if not prods:
                    break
                for p in prods:
                    reg = normalizar(p, cd)
                    out[(cd, reg["produto_id"])] = reg
                page += 1
                time.sleep(0.2)   # gentil com o servidor
    return list(out.values())


# ------------------------------------------------------------------- standalone
def _resumo(regs: list[dict]) -> None:
    print(f"\n  TOTAL registros: {len(regs)}")
    com_gtin = sum(1 for r in regs if r["gtin"])
    print(f"  com GTIN válido: {com_gtin}  ({100*com_gtin//max(len(regs),1)}%)")
    # comparação de preço por CD para os mesmos GTINs (prova do valor)
    porg: dict[str, dict[str, int]] = {}
    for r in regs:
        if r["gtin"] and r["preco_centavos"]:
            porg.setdefault(r["gtin"], {})[r["loja_cd"]] = r["preco_centavos"]
    multi = {g: v for g, v in porg.items() if len(v) > 1}
    print(f"  GTINs presentes em mais de 1 CD: {len(multi)}")
    for g, v in list(multi.items())[:8]:
        nome = next((r["descricao"] for r in regs if r["gtin"] == g), "")
        precos = "  ".join(f"cd{cd}=R${c/100:.2f}" for cd, c in sorted(v.items()))
        print(f"    {g}  {nome[:34]:34} {precos}")


def main() -> None:
    if not _TOKEN:
        print("ERRO: defina CARVALHO_TOKEN. Ex.:")
        print("  export CARVALHO_TOKEN='Bearer eyJ...'   (cole do cURL do navegador)")
        print("  export CARVALHO_SESSION='a2f4128f-...'  (o ?session= da URL)")
        return
    print("Coletando catálogo Carvalho (cd1 + cd2)...")
    regs = coletar()
    destino = "/tmp/catalogo_carvalho.json"
    with open(destino, "w", encoding="utf-8") as f:
        json.dump(regs, f, ensure_ascii=False, indent=2)
    _resumo(regs)
    print(f"\n  JSON salvo em {destino}")
    print("  (confira o arquivo e me diga; depois ligamos no banco com o Claude Code.)")


if __name__ == "__main__":
    main()
