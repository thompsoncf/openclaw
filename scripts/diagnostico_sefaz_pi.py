"""Diagnostico do scraping da NFC-e detalhada da SEFAZ-PI.

NAO grava nada no banco. So' testa SE da' pra acessar a pagina de uma nota e
extrair produtos+EAN. Roda no Render (rede liberada pra .gov.br), passando a
CHAVE de 44 digitos OU a URL completa do QR de uma nota real de Teresina.

Uso (no Render Shell):
    python -m scripts.diagnostico_sefaz_pi <CHAVE_44_DIGITOS>
    python -m scripts.diagnostico_sefaz_pi "<URL_COMPLETA_DO_QR>"

O que ele responde:
  1. Quais formatos de URL da SEFAZ-PI respondem (status HTTP).
  2. Se a resposta parece pagina de captcha (palavras suspeitas).
  3. Se aparecem codigos EAN/GTIN no HTML (13 digitos perto de "EAN"/"GTIN").
  4. Um trecho do HTML pra inspecao manual.
"""
from __future__ import annotations

import re
import ssl
import sys
import urllib.request

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9",
}

# formatos de URL candidatos da SEFAZ-PI (preenchidos com a chave)
def _candidatos(chave: str, url_completa: str | None) -> list[tuple[str, str]]:
    c = []
    if url_completa:
        c.append(("url_do_qr_inteira", url_completa))
    # consulta resumida por chave (varios padroes vistos em SEFAZ)
    c.append(("consulta_chNFe", f"https://www.sefaz.pi.gov.br/nfce/consulta?chNFe={chave}"))
    c.append(("qrcode_p_chave", f"https://www.sefaz.pi.gov.br/nfce/qrcode?p={chave}"))
    c.append(("consultarNFCe", f"https://www.sefaz.pi.gov.br/nfce/consultarNFCe?chNFe={chave}"))
    c.append(("nfce_index", f"https://www.sefaz.pi.gov.br/nfce/consulta/?chNFe={chave}"))
    return c


def _baixar(url: str) -> tuple[int, str]:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
        corpo = r.read().decode("utf-8", errors="replace")
        return r.status, corpo


def _tem_captcha(html: str) -> bool:
    low = html.lower()
    sinais = ["captcha", "recaptcha", "resolva o problema", "não sou um robô",
              "nao sou um robo", "g-recaptcha", "hcaptcha"]
    return any(s in low for s in sinais)


def _eans(html: str) -> list[str]:
    # procura 13 digitos (EAN-13) perto de palavras "ean"/"gtin"/"barras"
    achados = set()
    for m in re.finditer(r"(ean|gtin|barras)[^0-9]{0,30}(\d{8,14})", html, re.IGNORECASE):
        achados.add(m.group(2))
    # tambem qualquer 13 digitos solto (EAN tipico de supermercado)
    for m in re.finditer(r"\b(\d{13})\b", html):
        achados.add(m.group(1))
    return sorted(achados)[:15]


def _produtos_aprox(html: str) -> int:
    # heuristica: conta ocorrencias de "Qtde"/"Vl. Unit"/"Código" que marcam itens
    low = html.lower()
    return max(low.count("vl. unit"), low.count("qtde"), low.count("código:"),
               low.count("codigo:"))


def main():
    if len(sys.argv) < 2:
        print("uso: python -m scripts.diagnostico_sefaz_pi <CHAVE_44> | \"<URL_QR>\"")
        sys.exit(1)
    arg = sys.argv[1].strip()
    # extrai chave de 44 digitos do argumento (seja chave ou URL)
    m = re.search(r"(\d{44})", arg)
    chave = m.group(1) if m else None
    url_completa = arg if arg.lower().startswith("http") else None
    if not chave:
        print("Nao achei chave de 44 digitos no argumento.")
        sys.exit(1)
    print(f"Chave: {chave}")
    print(f"UF (pos 0-1): {chave[:2]}  (22=PI)")
    print(f"URL completa do QR fornecida: {'sim' if url_completa else 'nao'}")
    print("=" * 60)

    for nome, url in _candidatos(chave, url_completa):
        print(f"\n### {nome}\n{url}")
        try:
            status, html = _baixar(url)
            print(f"  status HTTP: {status}  | tamanho: {len(html)} chars")
            print(f"  parece captcha? {'SIM AVISO' if _tem_captcha(html) else 'nao'}")
            eans = _eans(html)
            print(f"  EANs encontrados: {len(eans)} {eans[:8] if eans else ''}")
            print(f"  marcadores de item (~qtd produtos): {_produtos_aprox(html)}")
            # mostra um pedaco pra inspecao (sem poluir)
            trecho = re.sub(r"\s+", " ", html)[:300]
            print(f"  trecho: {trecho}")
        except Exception as e:  # noqa: BLE001
            print(f"  ERRO: {type(e).__name__}: {e}")

    print("\n" + "=" * 60)
    print("LEITURA DO RESULTADO:")
    print("- Algum candidato com EANs > 0 e captcha=nao -> SCRAPING VIAVEL!")
    print("- Todos com captcha=SIM -> scraping automatico inviavel (foco no colaborativo).")
    print("- Todos status != 200 / erro -> URL errada ou precisa do hash do QR")
    print("  (rode de novo passando a URL COMPLETA do QR entre aspas).")
    print("\nMe manda essa saida inteira que eu interpreto e construo o scraper certo.")


if __name__ == "__main__":
    main()
