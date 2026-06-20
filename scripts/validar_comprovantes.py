"""Valida comprovantes .txt (modo texto, sem libs de imagem)."""
import json
import os
import sys

CAMBIO = 5.40
PRECO = {
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
}
MODELO_SONNET = os.environ.get("MODELO_FOTO", "claude-sonnet-4-6")
MODELO_HAIKU = os.environ.get("MODELO_TEXTO", "claude-haiku-4-5-20251001")
MODELOS = [("Sonnet", MODELO_SONNET), ("Haiku", MODELO_HAIKU)]

INSTRUCAO = """Voce le um COMPROVANTE BANCARIO brasileiro (Pix, TED, DOC, boleto ou
transferencia). NAO e' cupom fiscal - ignore qualquer ideia de itens/produtos.
Extraia SO' estes campos e responda SO' em JSON valido, sem texto fora, neste
formato exato:
{"tipo":"receita|despesa","valor":0.00,"data":"DD/MM/AAAA","contraparte":"nome de quem mandou ou recebeu","forma":"Pix|TED|DOC|boleto"}
Regra do tipo: se o dinheiro ENTROU pra quem ve o comprovante (recebimento) -> "receita";
se SAIU (pagamento/envio) -> "despesa". Se nao der pra ter certeza, use "receita".
Se algum campo nao aparecer, use null nesse campo."""

def _preco(mid):
    return PRECO.get(mid, PRECO["claude-sonnet-4-6"])

def custo_brl(mid, usage):
    pin, pout = _preco(mid)
    usd = usage.input_tokens / 1e6 * pin + usage.output_tokens / 1e6 * pout
    return usd * CAMBIO

def extrair(client, modelo_id, texto):
    resp = client.messages.create(
        model=modelo_id, max_tokens=600,
        messages=[{"role": "user", "content": [
            {"type": "text", "text": f"{INSTRUCAO}\n\n---COMPROVANTE---\n{texto}\n---FIM---"},
        ]}],
    )
    resposta = "".join(b.text for b in resp.content if b.type == "text").strip()
    for marca in ("```json", "```"):
        if resposta.startswith(marca):
            resposta = resposta[len(marca):]
    resposta = resposta.removesuffix("```").strip()
    try:
        dados = json.loads(resposta)
    except Exception:
        dados = {"tipo": None, "valor": None, "data": None, "contraparte": None,
                 "forma": None, "_raw": resposta[:160]}
    return dados, resp.usage

def _norm(s):
    return "".join(c for c in str(s or "").lower() if c.isalnum() or c == " ").strip()

def comparar(s, h):
    campos = ["tipo", "valor", "data", "contraparte", "forma"]
    difs = []
    criticos_ok = True
    for c in campos:
        vs, vh = s.get(c), h.get(c)
        if c == "contraparte":
            igual = _norm(vs) == _norm(vh)
        else:
            igual = vs == vh
        if not igual:
            difs.append(f"{c}: Sonnet={vs!r} vs Haiku={vh!r}")
            if c in ("valor", "data"):
                criticos_ok = False
    return difs, criticos_ok

def rodar_um(client, caminho):
    print("\n" + "=" * 72)
    print(f"COMPROVANTE: {caminho}")
    print("=" * 72)
    with open(caminho, "r", encoding="utf-8") as f:
        texto = f.read()

    res = {}
    for nome, mid in MODELOS:
        dados, usage = extrair(client, mid, texto)
        print(f"\n[{nome}]  tipo={dados.get('tipo')}  valor={dados.get('valor')}  "
              f"data={dados.get('data')}  forma={dados.get('forma')}")
        print(f"         contraparte={dados.get('contraparte')!r}  "
              f"tokens in={usage.input_tokens} out={usage.output_tokens}  "
              f"custo=R$ {custo_brl(mid, usage):.4f}")
        res[nome] = {"dados": dados, "usage": usage, "mid": mid}

    s, h = res["Sonnet"], res["Haiku"]
    difs, criticos_ok = comparar(s["dados"], h["dados"])
    tag = "IGUAL" if not difs else ("DIVERGE-CRITICO" if not criticos_ok else "DIVERGE-LEVE")
    print(f"\n  -- comparacao --  [{tag}]")
    for d in difs:
        print(f"    {d}")
    cs, ch = custo_brl(s["mid"], s["usage"]), custo_brl(h["mid"], h["usage"])
    if ch:
        print(f"  custo: Sonnet R$ {cs:.4f}  |  Haiku R$ {ch:.4f}  "
              f"(Haiku {cs/ch:.1f}x mais barato nesta leitura)")
    return tag, cs, ch

def main():
    if len(sys.argv) < 2:
        print("uso: python -m scripts.validar_comprovantes <comprovante.txt> [outros...]")
        return
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Falta ANTHROPIC_API_KEY na env.")
        return
    from anthropic import Anthropic
    client = Anthropic()
    placar = {"IGUAL": 0, "DIVERGE-LEVE": 0, "DIVERGE-CRITICO": 0}
    tot_s = tot_h = 0.0
    for caminho in sys.argv[1:]:
        if not os.path.exists(caminho):
            print(f"(pulado, nao existe) {caminho}"); continue
        try:
            tag, cs, ch = rodar_um(client, caminho)
            placar[tag] = placar.get(tag, 0) + 1
            tot_s += cs; tot_h += ch
        except Exception as e:
            print(f"ERRO no comprovante {caminho}: {e}")
    print("\n" + "=" * 72)
    n = sum(placar.values()) or 1
    print(f"RESUMO ({n}):  iguais={placar['IGUAL']}  "
          f"diverge-leve={placar['DIVERGE-LEVE']}  diverge-CRITICO={placar['DIVERGE-CRITICO']}")
    if tot_h:
        print(f"custo total: Sonnet R$ {tot_s:.4f}  |  Haiku R$ {tot_h:.4f}  "
              f"(Haiku {tot_s/tot_h:.1f}x mais barato)")
    print("\nLeitura: 'valor' e 'data' sao os CRITICOS (e' dinheiro).")

if __name__ == "__main__":
    main()
