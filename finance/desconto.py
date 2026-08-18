"""Desconto do orçamento de serviço — por item e no total.

PONTO ÚNICO DA REGRA. Três lugares precisam chegar no mesmo número: a tela que o
dono usa pra negociar (JavaScript), a folha que o cliente lê, e o financeiro que
gera os títulos. Duas leituras diferentes de "quanto é o desconto" seriam o começo
de dois números — foi assim que um orçamento de produção ficou com parcelas
somando R$ 12.105 e total de R$ 9.405.

Tudo puro: centavos entram, centavos saem, nada toca banco.

DUAS REGRAS, e elas são as decisões que este módulo existe pra fixar:

1. DESCONTOS ENCADEIAM, NÃO SOMAM. O desconto do total incide sobre o subtotal
   JÁ descontado item a item, não sobre o bruto. A alternativa dá um número
   diferente (R$ 98 no exemplo do mockup) e a diferença é invisível na tela —
   quando dois caminhos plausíveis divergem em silêncio, vale o que a pessoa vê:
   ela deu 10% no que restou depois de negociar item a item.

2. NO ITEM, O DESCONTO É SEMPRE UM PERCENTUAL. No modo recorrente cada linha tem
   setup E mensalidade, e `fechar_orcamento` gera um título de cada — se o
   desconto virasse um valor único, dividir de volta entre as duas pontas seria
   chute. Então R$ digitado no item é convertido no percentual equivalente da
   contribuição daquele item ao primeiro ano, e esse percentual cai igual nos
   dois. A proporção entre setup e mensal é preservada por construção.

No TOTAL o desconto pode ser valor mesmo: ali existe um número só pra descontar.
"""
from __future__ import annotations

TIPOS = ("pct", "valor")

# Quantos meses de mensalidade entram no "primeiro ano" — é o mesmo 12 que a tela
# usa pra montar `ano1`, e está aqui pra as duas contas não divergirem.
MESES_ANO1 = 12


def _int(v) -> int:
    """Número que veio da tela vira int sem explodir. Campo vazio, None e texto
    solto valem zero — a tela é a fonte, e ela erra."""
    try:
        return int(round(float(v or 0)))
    except (TypeError, ValueError):
        return 0


def _pct(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _tipo(v) -> str:
    v = (str(v or "")).strip().lower()
    return v if v in TIPOS else "pct"


def quanto_desconta(base: int, tipo, pct, valor) -> int:
    """Quanto este desconto tira de `base`, em centavos.

    NUNCA negativo e NUNCA maior que a base: desconto que ultrapassa o valor
    viraria acréscimo, e um item de valor negativo contamina total, margem,
    parcelas e título. É o teto que dispensa configuração — o outro teto, o de
    política comercial ("avisar acima de 20%"), é decisão da casa e não mora aqui.
    """
    base = max(0, _int(base))
    if base <= 0:
        return 0
    if _tipo(tipo) == "valor":
        d = _int(valor)
    else:
        d = int(round(base * min(100.0, max(0.0, _pct(pct))) / 100.0))
    return max(0, min(base, d))


# ------------------------------------------------------------------- por item

def contribuicao(item) -> int:
    """O que esta linha soma ao primeiro ano, em centavos — a base do desconto dela.

    Evento: só o valor da linha (qtd × unitário já vem somado em `setup`).
    Recorrente: setup + mensalidade × 12, que é como a tela monta o `ano1`.
    """
    return max(0, _int(item.get("setup")) * 100) + \
        max(0, _int(item.get("mensal")) * 100) * MESES_ANO1


def percentual_do_item(item) -> float:
    """O desconto da linha COMO PERCENTUAL, sempre — mesmo quando foi digitado em
    reais. É a conversão que permite descontar setup e mensalidade na mesma
    proporção, e é o motivo de o financeiro continuar sabendo separar as duas."""
    base = contribuicao(item)
    if base <= 0:
        return 0.0
    if _tipo(item.get("desc_tipo")) == "pct":
        return min(100.0, max(0.0, _pct(item.get("desc_val"))))
    # digitado em reais: vira o percentual equivalente da contribuição da linha
    reais = max(0, _int(item.get("desc_val"))) * 100
    return min(100.0, 100.0 * reais / base)


def liquido_do_item(item) -> dict:
    """A linha depois do desconto dela: setup e mensal líquidos, em CENTAVOS, mais
    quanto foi descontado. Devolve os dois separados porque é assim que o
    financeiro precisa deles."""
    p = percentual_do_item(item)
    setup = max(0, _int(item.get("setup"))) * 100
    mensal = max(0, _int(item.get("mensal"))) * 100
    setup_liq = int(round(setup * (100.0 - p) / 100.0))
    mensal_liq = int(round(mensal * (100.0 - p) / 100.0))
    return {"pct": p, "setup": setup_liq, "mensal": mensal_liq,
            "desconto_setup": setup - setup_liq,
            "desconto_mensal": mensal - mensal_liq,
            "desconto": (setup - setup_liq) + (mensal - mensal_liq) * MESES_ANO1}


def somar_itens(itens) -> dict:
    """Os itens somados, em centavos: bruto, desconto e líquido, com setup e
    mensalidade sempre separados."""
    b_s = b_m = l_s = l_m = 0
    for it in (itens or []):
        if not isinstance(it, dict):
            continue
        b_s += max(0, _int(it.get("setup"))) * 100
        b_m += max(0, _int(it.get("mensal"))) * 100
        liq = liquido_do_item(it)
        l_s += liq["setup"]
        l_m += liq["mensal"]
    return {"bruto_setup": b_s, "bruto_mensal": b_m,
            "setup": l_s, "mensal": l_m,
            "desconto_setup": b_s - l_s, "desconto_mensal": b_m - l_m,
            "desconto": (b_s - l_s) + (b_m - l_m) * MESES_ANO1}


# --------------------------------------------------------------- total do doc

def totais(itens, *, tipo="pct", pct=0, valor=0,
           extra_setup: int = 0, extra_mensal: int = 0) -> dict:
    """A conta inteira, na ordem que a tela mostra.

    `extra_setup`/`extra_mensal` (centavos) são o que o modo recorrente soma fora
    das linhas — infraestrutura, integrações, canais, suporte dedicado. Eles
    entram no subtotal e por isso RECEBEM o desconto final, mas não recebem
    desconto por item (não são item).

    Devolve tudo em centavos, incluindo as pontas separadas: é delas que saem os
    títulos de setup e de mensalidade.
    """
    it = somar_itens(itens)
    setup = it["setup"] + max(0, _int(extra_setup))
    mensal = it["mensal"] + max(0, _int(extra_mensal))
    subtotal = setup + mensal * MESES_ANO1

    desc_final = quanto_desconta(subtotal, tipo, pct, valor)
    # o desconto final cai proporcionalmente nas duas pontas, pelo mesmo motivo do
    # desconto de item: o financeiro precisa de setup e mensal separados.
    p = (100.0 * desc_final / subtotal) if subtotal > 0 else 0.0
    setup_liq = int(round(setup * (100.0 - p) / 100.0))
    mensal_liq = int(round(mensal * (100.0 - p) / 100.0))

    return {
        "bruto": it["bruto_setup"] + it["bruto_mensal"] * MESES_ANO1
                 + max(0, _int(extra_setup)) + max(0, _int(extra_mensal)) * MESES_ANO1,
        "desconto_itens": it["desconto"],
        "subtotal": subtotal,
        "desconto_final": desc_final,
        "desconto_total": it["desconto"] + desc_final,
        "total": subtotal - desc_final,
        # as pontas, já líquidas — o que vira título
        "setup": setup_liq,
        "mensal": mensal_liq,
    }
