"""Coletor de precos da API publica Menor Preco (Nota Parana/Celepar, que tambem
serve o PI). Busca precos de produtos por cidade/regiao e grava no banco de ouro
com fonte='api_sefaz'.

DECISOES (acordadas com o produto):
- Cidade: Teresina-PI (geohash calculado).
- Lojas: NAO cria loja. Grava o preco com o nome da loja no campo `mercado`
  (loja_id fica nulo). A API nao fornece CNPJ, entao nao da' pra casar com a
  tabela lojas com seguranca.
- Filtro anti-lixo: so' grava produtos que tenham GTIN (codigo de barras) E cujo
  NCM bata com a familia esperada do termo. Isso corta micanga/pipoca/vinagre/
  pratos de restaurante que aparecem numa busca por "arroz". Banco limpo > cheio.

IMPORTANTE - LIMITES E RISCOS (ja' alinhados):
- API interna nao-oficial pra terceiros. Usar com MODERACAO (delay entre chamadas,
  poucos produtos por rodada). Pode ser bloqueada ou mudar sem aviso.
- Tratar como fonte COMPLEMENTAR/experimental. A fundacao do banco sao os cupons
  dos clientes.

Uso (no Render Shell, onde o dominio provavelmente esta' liberado):
    python -m scripts.coletar_menorpreco            # Teresina, lista padrao
    python -m scripts.coletar_menorpreco arroz feijao   # produtos especificos
"""
import json
import time
import urllib.parse
import urllib.request
import unicodedata
import logging

from db.conexao import get_pool
from finance.geohash import geohash_cidade
from finance.models import normalizar_descricao

_log = logging.getLogger("openclaw.precos")

_BASE = "https://menorpreco.notaparana.pr.gov.br/api/v1"
_UA = "OpenClaw/1.0 (assistente financeiro pessoal)"
_TIMEOUT = 12
_DELAY = 1.5          # segundos entre chamadas (gentil com o servidor)
_MAX_PAGINAS = 6      # ate' 6 paginas por produto (cada pagina ~40 itens)

# familias de NCM esperadas por termo (prefixo do NCM). Corta o lixo.
# arroz = 1006; feijao = 0713; cafe = 0901; acucar = 1701; leite = 0401;
# farinha/flocao = 1102/1104; oleo = 1507/1512; macarrao = 1902.
_NCM_ESPERADO = {
    "arroz": ("1006",),
    "feijao": ("0713",),
    "feijao": ("0713",),
    "cafe": ("0901",),
    "cafe": ("0901",),
    "acucar": ("1701",),
    "acucar": ("1701",),
    "leite": ("0401", "0402"),
    "farinha": ("1101", "1102", "1106"),
    "flocao": ("1102", "1104"),
    "flocao": ("1102", "1104"),
    "oleo": ("1507", "1508", "1512", "1515"),
    "oleo": ("1507", "1508", "1512", "1515"),
    "macarrao": ("1902",),
    "macarrao": ("1902",),
    "cuscuz": ("1102", "1104", "1904"),
}

# lista padrao de produtos de cesta basica pra coletar
_LISTA_PADRAO = ["arroz", "feijao", "cafe", "acucar", "leite", "farinha",
                 "oleo", "macarrao"]


def _http_get(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": _UA,
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _ncm_ok(termo: str, ncm: str) -> bool:
    """True se o NCM bate com a familia esperada do termo (ou se nao temos regra)."""
    esperados = _NCM_ESPERADO.get(termo.lower())
    if not esperados:
        return True  # sem regra: aceita (mas o GTIN ainda filtra)
    return any((ncm or "").startswith(p) for p in esperados)


def _montar_endereco(est: dict) -> str:
    partes = [est.get("tp_logr"), est.get("nm_logr"), est.get("nr_logr"),
              est.get("bairro")]
    return ", ".join(p for p in partes if p and str(p).strip())


def coletar_produto(termo: str, local: str, conn, raio: int = 5) -> dict:
    """Coleta precos de um produto, paginando. Grava os que passam no filtro.

    Filtro: precisa ter GTIN E o NCM bater com a familia do termo.
    Grava com fonte='api_sefaz', loja_id nulo, mercado=nome da loja.
    """
    gravados = 0
    descartados = 0
    vistos = set()  # (descricao_norm, valor, mercado) pra dedup na rodada
    for pagina in range(_MAX_PAGINAS):
        offset = pagina * 40
        params = urllib.parse.urlencode({
            "local": local, "termo": termo, "offset": offset,
            "raio": raio, "data": -1, "ordem": 0})
        url = f"{_BASE}/produtos?{params}"
        try:
            data = _http_get(url)
        except Exception as e:  # noqa: BLE001
            _log.warning("menorpreco: falha em '%s' offset %s: %s", termo, offset, e)
            break
        produtos = data.get("produtos") or []
        if not produtos:
            break
        for p in produtos:
            gtin = (p.get("gtin") or "").strip()
            ncm = (p.get("ncm") or "").strip()
            desc = (p.get("desc") or "").strip()
            valor = p.get("valor")
            # FILTRO ANTI-LIXO: exige GTIN e NCM da familia certa
            if not gtin:
                descartados += 1
                continue
            if not _ncm_ok(termo, ncm):
                descartados += 1
                continue
            if not desc or valor in (None, "", "0", "0.00"):
                descartados += 1
                continue
            try:
                centavos = int(round(float(valor) * 100))
            except (TypeError, ValueError):
                descartados += 1
                continue
            if centavos <= 0:
                descartados += 1
                continue
            est = p.get("estabelecimento") or {}
            nome_loja = (est.get("nm_fan") or est.get("nm_emp") or "").strip()
            mun = (est.get("mun") or "").strip()
            uf = (est.get("uf") or "").strip()
            endereco = _montar_endereco(est)
            norm = normalizar_descricao(desc)
            chave = (norm, centavos, nome_loja)
            if chave in vistos:
                continue
            vistos.add(chave)
            # grava preco (sem criar loja; mercado = nome da loja)
            mercado = nome_loja or "-"
            if mun:
                mercado = f"{mercado} - {mun}/{uf}" if uf else f"{mercado} - {mun}"
            regiao = f"{mun}/{uf}" if mun else (uf or None)
            try:
                conn.execute(
                    """insert into precos_observados
                       (descricao_norm, descricao_original, valor_unitario_centavos,
                        mercado, regiao, gtin, fonte, data_compra)
                       values (%s,%s,%s,%s,%s,%s,'api_sefaz', current_date)""",
                    (norm, desc, centavos, mercado, regiao, gtin or None))
                gravados += 1
            except Exception as e:  # noqa: BLE001
                _log.warning("menorpreco: falha ao gravar '%s': %s", desc, e)
        conn.commit()
        time.sleep(_DELAY)
    return {"termo": termo, "gravados": gravados, "descartados": descartados}


def coletar(cidade: str = "teresina", termos: list[str] | None = None,
            raio: int = 5) -> dict:
    local = geohash_cidade(cidade)
    if not local:
        return {"erro": f"cidade desconhecida: {cidade}"}
    termos = termos or _LISTA_PADRAO
    resultado = {"cidade": cidade, "local": local, "produtos": []}
    pool = get_pool()
    with pool.connection() as conn:
        for termo in termos:
            r = coletar_produto(termo, local, conn, raio=raio)
            resultado["produtos"].append(r)
            _log.info("menorpreco %s: %s gravados, %s descartados",
                      termo, r["gravados"], r["descartados"])
    resultado["total_gravados"] = sum(p["gravados"] for p in resultado["produtos"])
    return resultado


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    termos = sys.argv[1:] or None
    r = coletar(termos=termos)
    if r.get("erro"):
        print("ERRO:", r["erro"])
    else:
        print(f"Cidade: {r['cidade']} (local={r['local']})")
        for p in r["produtos"]:
            print(f"  {p['termo']}: {p['gravados']} gravados, {p['descartados']} descartados")
        print(f"TOTAL gravados: {r['total_gravados']}")
