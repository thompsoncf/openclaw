"""Sonda da API Menor Preço (SEFAZ/Nota Parana) - MISSAO DE RECONHECIMENTO.

NAO faz parte do sistema. E' um script de teste pra rodar NO RENDER (onde a
rede e' liberada), descobrir QUAL endpoint responde, com QUAIS parametros, e
QUAL o formato do JSON. Com isso em maos, construimos o cliente de verdade.

Como rodar no Render:
  - Suba este arquivo no repo (git push).
  - No Render, abra um Shell no servico openclaw-web e rode:  python sonda_sefaz.py
  - Copie TODA a saida e cole no chat.

So' LE dados publicos de preco. Nao envia nada, nao muda nada.
"""
import json
import urllib.request
import urllib.error
import ssl

# Teresina-PI (centro) - a API usa lat/lon pra achar mercados num raio
LAT, LON = -5.0892, -42.8016
TERMO = "arroz"
RAIO_KM = 15

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "pt-BR,pt;q=0.9",
    "Referer": "https://menorpreco.notaparana.pr.gov.br/",
    "Origin": "https://menorpreco.notaparana.pr.gov.br",
    "X-Requested-With": "XMLHttpRequest",
}


def _candidatos():
    return [
        f"https://menorpreco.notaparana.pr.gov.br/api/v1/produtos?local={LAT},{LON}&termo={TERMO}&raio={RAIO_KM}&offset=0",
        f"https://menorpreco.notaparana.pr.gov.br/api/v1/produtos?termo={TERMO}&local={LAT},{LON}&raio={RAIO_KM}",
        f"https://menorpreco.notaparana.pr.gov.br/api/produtos?termo={TERMO}&latitude={LAT}&longitude={LON}&raio={RAIO_KM}",
        f"https://menorpreco.notaparana.pr.gov.br/produtos?termo={TERMO}&local={LAT},{LON}&raio={RAIO_KM}",
        f"https://menorpreco.notaparana.pr.gov.br/app/produtos?termo={TERMO}&local={LAT},{LON}",
        "https://menorpreco.notaparana.pr.gov.br/api/v1/produtos?gtin=7891234567890&local=-5.0892,-42.8016&raio=15",
        "https://menorpreco.notaparana.pr.gov.br/",
    ]


def _testar(url: str):
    print("\n" + "=" * 70)
    print("GET", url)
    req = urllib.request.Request(url, headers=_HEADERS)
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
            status = r.status
            ctype = r.headers.get("Content-Type", "")
            body = r.read(4000).decode("utf-8", "replace")
            print(f"STATUS {status} | Content-Type: {ctype}")
            if "json" in ctype.lower() or body.lstrip().startswith(("{", "[")):
                try:
                    dados = json.loads(body)
                    print("JSON OK! Estrutura:")
                    print(json.dumps(dados, indent=2, ensure_ascii=False)[:2500])
                    return ("JSON", url)
                except Exception as e:  # noqa: BLE001
                    print("Parecia JSON mas nao parseou:", e)
                    print(body[:1500])
            else:
                print("(nao-JSON) primeiros 600 chars:")
                print(body[:600])
            return (f"{status}", url)
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code} - {e.reason}")
        try:
            corpo = e.read(500).decode("utf-8", "replace")
            print("corpo do erro:", corpo[:300])
        except Exception:  # noqa: BLE001
            pass
        return (f"HTTP {e.code}", url)
    except Exception as e:  # noqa: BLE001
        print("FALHOU:", type(e).__name__, str(e)[:200])
        return ("ERRO", url)


def main():
    print("SONDA MENOR PRECO / SEFAZ - reconhecimento de endpoint")
    print(f"Local de teste: Teresina-PI ({LAT}, {LON}) | termo: '{TERMO}' | raio: {RAIO_KM}km")
    resultados = []
    for url in _candidatos():
        resultados.append(_testar(url))
    print("\n" + "#" * 70)
    print("RESUMO:")
    for status, url in resultados:
        print(f"  [{status}] {url[:90]}")
    print("\nProcure por [JSON] acima - esse e' o endpoint que funciona.")
    print("Cole TODA esta saida no chat pra montarmos o cliente real.")


if __name__ == "__main__":
    main()
