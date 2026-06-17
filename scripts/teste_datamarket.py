"""Teste rapido da API Data Market (Cnova Tech).

Rodar no RENDER SHELL (a chave fica na env de la', a rede alcanca a API):
    python -m scripts.teste_datamarket 7894900011609
Sem argumento, usa um EAN de exemplo (Coca-Cola). Dica: teste com um EAN que voce
JA' tem no cupom (ex: a fralda Pampers) pra comparar o preco da API vs o que pagou
em Teresina.
"""
import sys

from finance.datamarket import consultar_preco_ean, disponivel


def _brl(c):
    return f"R$ {c/100:.2f}" if c is not None else "-"


def main():
    if not disponivel():
        print("DATAMARKET_API_KEY nao esta' na env. Configure no Render e tente de novo.")
        return
    ean = sys.argv[1] if len(sys.argv) > 1 else "7894900011609"
    print(f"Consultando EAN {ean} ...")
    r = consultar_preco_ean(ean)
    if not r:
        print("Nada retornado (EAN nao encontrado na base, chave invalida, ou falha "
              "de rede). Veja os logs do servico.")
        return
    print(f"\n{r['produto']}  ({r['marca']} / {r['departamento']})")
    print(f"  menor {_brl(r['menor_centavos'])} | tipico {_brl(r['tipico_centavos'])} "
          f"| media {_brl(r['media_centavos'])} | maior {_brl(r['maior_centavos'])}")
    print(f"  lojas: {r['lojas']} | regioes: {r['regioes']} | disponibilidade: "
          f"{r['disponibilidade_pct']}% | variacao: {r['variacao_pct']}%")
    mb = r.get("mais_barata")
    if mb:
        print(f"  mais barata (em estoque): {mb['loja']}/{mb['uf']} "
              f"{_brl(mb['preco_centavos'])} ({mb['canal']}, {mb['atualizado']})")


if __name__ == "__main__":
    main()
