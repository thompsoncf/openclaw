"""Compara a extracao de cupom: Sonnet 4.6 vs Haiku 4.5.

Roda o MESMO cupom (foto) nos dois modelos e mostra lado a lado: itens, precos,
EAN e o custo (tokens -> R$). Serve pra decidir, COM DADO REAL, se da' pra trocar
a leitura de cupom pro Haiku (~3x mais barato) sem perder precisao no banco "ouro".

Uso (local ou Render Shell, com ANTHROPIC_API_KEY na env):
    python -m scripts.comparar_modelos_cupom caminho/do/cupom.jpg
    python -m scripts.comparar_modelos_cupom c1.jpg c2.jpg c3.jpg   (varios)
"""
import base64
import json
import mimetypes
import os
import sys

CAMBIO = 5.40   # US$ -> R$ (ajuste se quiser)
# (preco_entrada, preco_saida) em US$ por milhao de tokens
PRECO = {
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5":  (1.0, 5.0),
}
MODELOS = [("Sonnet 4.6", "claude-sonnet-4-6"), ("Haiku 4.5", "claude-haiku-4-5")]

INSTRUCAO = """Voce e' um leitor de cupom fiscal (NFC-e) brasileiro. Extraia TODOS
os itens do cupom desta imagem. Para cada item: descricao legivel (expanda
abreviacoes, mantenha marca/tamanho), quantidade, valor unitario (R$), valor total
(R$), unidade (UN ou KG) e o codigo de barras da coluna COD se existir (8 a 14
digitos; ignore codigos curtos de balanca). Responda SO' em JSON valido, sem texto
fora, neste formato exato:
{"loja":"nome","cnpj":"so digitos ou null","itens":[
 {"descricao":"...","quantidade":1,"valor_unitario":0.00,"valor_total":0.00,
  "unidade":"UN","codigo":"..."}]}"""


def _norm(s):
    return "".join(c for c in (s or "").lower() if c.isalnum() or c == " ").strip()


def extrair(client, modelo_id, img_b64, media_type):
    resp = client.messages.create(
        model=modelo_id, max_tokens=4000,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64",
             "media_type": media_type, "data": img_b64}},
            {"type": "text", "text": INSTRUCAO},
        ]}],
    )
    texto = "".join(b.text for b in resp.content if b.type == "text").strip()
    for marca in ("```json", "```"):
        if texto.startswith(marca):
            texto = texto[len(marca):]
    texto = texto.removesuffix("```").strip()
    try:
        dados = json.loads(texto)
    except Exception:  # noqa: BLE001
        dados = {"loja": "(JSON invalido)", "cnpj": None, "itens": [], "_raw": texto[:200]}
    return dados, resp.usage


def custo_brl(modelo_id, usage):
    pin, pout = PRECO[modelo_id]
    usd = usage.input_tokens / 1e6 * pin + usage.output_tokens / 1e6 * pout
    return usd * CAMBIO


def comparar(a, b):
    """a, b: listas de itens dos dois modelos. Casa por descricao normalizada."""
    ia = {_norm(i.get("descricao")): i for i in a}
    ib = {_norm(i.get("descricao")): i for i in b}
    todas = sorted(set(ia) | set(ib))
    iguais_preco = iguais_ean = so_a = so_b = difere = 0
    linhas = []
    for k in todas:
        x, y = ia.get(k), ib.get(k)
        if x and not y:
            so_a += 1; linhas.append(("so SONNET", x.get("descricao"), "", ""))
        elif y and not x:
            so_b += 1; linhas.append(("so HAIKU", y.get("descricao"), "", ""))
        else:
            pa, pb = x.get("valor_total"), y.get("valor_total")
            ca, cb = (x.get("codigo") or ""), (y.get("codigo") or "")
            mp = (pa == pb)
            me = (ca == cb)
            iguais_preco += mp; iguais_ean += me
            if not (mp and me):
                difere += 1
                linhas.append(("DIFERE", x.get("descricao"),
                               f"R${pa} vs R${pb}" if not mp else "preco ok",
                               f"EAN {ca or '-'} vs {cb or '-'}" if not me else "EAN ok"))
    return {"total": len(todas), "iguais_preco": iguais_preco, "iguais_ean": iguais_ean,
            "so_a": so_a, "so_b": so_b, "difere": difere, "linhas": linhas}


def rodar_um(client, caminho):
    print("\n" + "=" * 70)
    print(f"CUPOM: {caminho}")
    print("=" * 70)
    with open(caminho, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()
    media_type = mimetypes.guess_type(caminho)[0] or "image/jpeg"

    res = {}
    for nome, mid in MODELOS:
        dados, usage = extrair(client, mid, img_b64, media_type)
        itens = dados.get("itens", [])
        com_ean = sum(1 for i in itens if i.get("codigo"))
        print(f"\n[{nome}]  loja={dados.get('loja')!r}  cnpj={dados.get('cnpj')}")
        print(f"  itens={len(itens)}  com EAN={com_ean}  "
              f"tokens in={usage.input_tokens} out={usage.output_tokens}  "
              f"custo=R$ {custo_brl(mid, usage):.4f}")
        res[nome] = {"itens": itens, "usage": usage, "mid": mid}

    s, h = res["Sonnet 4.6"], res["Haiku 4.5"]
    cmp = comparar(s["itens"], h["itens"])
    print(f"\n  -- comparacao --  itens em comum: {cmp['total']}")
    print(f"  preco igual: {cmp['iguais_preco']}  | EAN igual: {cmp['iguais_ean']}  "
          f"| so Sonnet: {cmp['so_a']}  | so Haiku: {cmp['so_b']}  | divergem: {cmp['difere']}")
    for tag, desc, p, e in cmp["linhas"]:
        print(f"    [{tag}] {desc}  {p}  {e}")

    cs, ch = custo_brl(s["mid"], s["usage"]), custo_brl(h["mid"], h["usage"])
    if ch:
        print(f"\n  custo: Sonnet R$ {cs:.4f}  |  Haiku R$ {ch:.4f}  "
              f"(Haiku {cs/ch:.1f}x mais barato neste cupom)")


def main():
    if len(sys.argv) < 2:
        print("uso: python -m scripts.comparar_modelos_cupom <cupom.jpg> [outros...]")
        return
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Falta ANTHROPIC_API_KEY na env.")
        return
    from anthropic import Anthropic
    client = Anthropic()
    for caminho in sys.argv[1:]:
        if not os.path.exists(caminho):
            print(f"(pulado, nao existe) {caminho}"); continue
        try:
            rodar_um(client, caminho)
        except Exception as e:  # noqa: BLE001
            print(f"ERRO no cupom {caminho}: {e}")
    print("\nLeitura: se 'EAN igual' e 'preco igual' baterem com o total de itens em")
    print("todos os cupons, o Haiku le' tao bem quanto o Sonnet -> pode trocar e poupar")
    print("3x. Se divergir (EAN/preco trocado, item pulado), mantenha o Sonnet no cupom.")


if __name__ == "__main__":
    main()
