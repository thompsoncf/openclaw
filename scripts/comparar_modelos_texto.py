"""Compara a DECISAO DE FERRAMENTA no texto: Sonnet 4.6 vs Haiku 4.5.

O roteamento ja' existe (core/agent.py): foto->Sonnet, texto->Haiku. O risco de
usar Haiku no texto NAO e' "ler errado" -- e' escolher a ferramenta errada ou com
argumento errado (registrar gasto no valor/categoria errados, chamar saldo quando
era relatorio, etc). Este script isola exatamente isso: roda as MESMAS mensagens
nos dois modelos, com a persona e os schemas REAIS do agente, e mostra lado a lado
qual ferramenta cada um chamou e com quais argumentos. NAO executa as ferramentas
(nao toca no banco) -- so' inspeciona a decisao do modelo.

Uso (local ou Render Shell, com ANTHROPIC_API_KEY na env):
    python -m scripts.comparar_modelos_texto
    python -m scripts.comparar_modelos_texto "gastei 30 no posto" "qual meu saldo"
    PAPEL=restrito python -m scripts.comparar_modelos_texto   (testa a Mary)

Os modelos vem das MESMAS envs que a producao usa (MODELO_FOTO / MODELO_TEXTO),
entao a comparacao reflete o que esta' no ar. Sem cache aqui (chamada unica): os
numeros absolutos sao maiores que em producao, mas a RAZAO Haiku vs Sonnet vale.
"""
import json
import os
import sys
import unicodedata

CAMBIO = 5.40   # US$ -> R$
# (preco_entrada, preco_saida) em US$ por milhao de tokens
PRECO = {
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5":  (1.0, 5.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
}

# Mensagens padrao: cobrem os intents principais que mapeiam pros 10 schemas.
# Inclui duas "pegadinhas" (oi, obrigado) pra ver se o Haiku chama ferramenta a' toa.
MENSAGENS_PADRAO = [
    "gastei 50 reais no mercado hoje",
    "recebi 2000 de salario ontem",
    "qual meu saldo",
    "quanto gastei esse mes",
    "me mostra os ultimos lancamentos",
    "apaga o ultimo gasto",
    "paguei 45,90 na farmacia com cartao",
    "oi tudo bem",
    "obrigado",
]


def _preco(mid):
    return PRECO.get(mid, PRECO["claude-sonnet-4-6"])


def custo_brl(mid, usage):
    pin, pout = _preco(mid)
    usd = usage.input_tokens / 1e6 * pin + usage.output_tokens / 1e6 * pout
    return usd * CAMBIO


def decidir(client, modelo_id, persona, schemas, texto):
    """Uma chamada, sem executar ferramenta. Devolve (lista_de_tools, txt, usage).
    Cada tool e' (nome, input_dict)."""
    resp = client.messages.create(
        model=modelo_id, max_tokens=1024,
        system=persona,
        tools=schemas,
        messages=[{"role": "user", "content": texto}],
    )
    tools = []
    txt = []
    for b in resp.content:
        if b.type == "tool_use":
            tools.append((b.name, dict(b.input)))
        elif b.type == "text":
            txt.append(b.text)
    return tools, " ".join(txt).strip(), resp.usage


def _sem_acento(s):
    """Remove acentos pra comparacao (cartao == cartão)."""
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def _norm_input(d):
    """Normaliza o input pra comparar: chaves ordenadas, strings em minuscula e
    sem acento, ignora 'data'/'origem' (defaults variam sem mudar a intencao)."""
    out = {}
    for k, v in (d or {}).items():
        if k in ("data", "origem"):
            continue
        out[k] = _sem_acento(v.lower().strip()) if isinstance(v, str) else v
    return json.dumps(out, sort_keys=True, ensure_ascii=False)


def _fmt_tools(tools):
    if not tools:
        return "(sem ferramenta / resposta em texto)"
    return "  ".join(f"{n}({json.dumps(i, ensure_ascii=False)})" for n, i in tools)


def comparar_decisao(ts, th):
    """Mesmo conjunto de ferramentas? Mesmos argumentos-chave?"""
    ns, nh = [t[0] for t in ts], [t[0] for t in th]
    if sorted(ns) != sorted(nh):
        return "DIVERGE-TOOL"
    # mesmas ferramentas: confere os inputs (casa por nome)
    ds = {n: _norm_input(i) for n, i in ts}
    dh = {n: _norm_input(i) for n, i in th}
    if ds != dh:
        return "DIVERGE-ARGS"
    return "IGUAL"


def main():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Falta ANTHROPIC_API_KEY na env.")
        return

    papel = os.environ.get("PAPEL", "dono")
    modelo_sonnet = os.environ.get("MODELO_FOTO", "claude-sonnet-4-6")
    modelo_haiku = os.environ.get("MODELO_TEXTO", "claude-haiku-4-5-20251001")
    mensagens = sys.argv[1:] or MENSAGENS_PADRAO

    # Persona + schemas REAIS do agente (pool=None: nunca executamos as ferramentas)
    from finance.livro_caixa import LivroCaixa
    from finance.tools import construir_ferramentas
    from finance.agente_financeiro import _persona
    livro = LivroCaixa(None, 0)
    persona = _persona(papel)
    schemas = [f.schema() for f in construir_ferramentas(livro, None, papel, None, None)]

    from anthropic import Anthropic
    client = Anthropic()

    print(f"papel={papel}  |  Sonnet={modelo_sonnet}  Haiku={modelo_haiku}")
    print(f"ferramentas expostas: {[s['name'] for s in schemas]}\n")

    tot_s = tot_h = 0.0
    placar = {"IGUAL": 0, "DIVERGE-TOOL": 0, "DIVERGE-ARGS": 0}
    for msg in mensagens:
        try:
            ts, txt_s, us = decidir(client, modelo_sonnet, persona, schemas, msg)
            th, txt_h, uh = decidir(client, modelo_haiku, persona, schemas, msg)
        except Exception as e:  # noqa: BLE001
            print(f'>>> "{msg}"  ERRO: {e}\n'); continue
        veredito = comparar_decisao(ts, th)
        placar[veredito] = placar.get(veredito, 0) + 1
        cs, ch = custo_brl(modelo_sonnet, us), custo_brl(modelo_haiku, uh)
        tot_s += cs; tot_h += ch
        print("=" * 72)
        print(f'>>> "{msg}"   [{veredito}]')
        print(f"  SONNET: {_fmt_tools(ts)}")
        if txt_s:
            print(f"          txt: {txt_s[:120]}")
        print(f"  HAIKU : {_fmt_tools(th)}")
        if txt_h:
            print(f"          txt: {txt_h[:120]}")
        print(f"  custo: Sonnet R$ {cs:.4f}  |  Haiku R$ {ch:.4f}")

    print("\n" + "=" * 72)
    n = sum(placar.values()) or 1
    print(f"RESUMO ({n} mensagens):  iguais={placar['IGUAL']}  "
          f"diverge-tool={placar['DIVERGE-TOOL']}  diverge-args={placar['DIVERGE-ARGS']}")
    if tot_h:
        print(f"custo total: Sonnet R$ {tot_s:.4f}  |  Haiku R$ {tot_h:.4f}  "
              f"(Haiku {tot_s/tot_h:.1f}x mais barato no texto)")
    print("\nLeitura: se TUDO der IGUAL, o Haiku decide a ferramenta como o Sonnet,")
    print("o roteamento de texto e' seguro e poupa ~3x. Cada DIVERGE-TOOL e' uma")
    print("intencao que o Haiku errou de ferramenta (grave); DIVERGE-ARGS e' valor/")
    print("categoria/descricao diferente (conferir caso a caso).")


if __name__ == "__main__":
    main()
