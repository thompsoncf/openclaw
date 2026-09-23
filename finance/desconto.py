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

2. NO ITEM, O DESCONTO É UM PERCENTUAL (com uma exceção, `por_mes`). No modo recorrente cada linha tem
   setup E mensalidade, e `fechar_orcamento` gera um título de cada — se o
   desconto virasse um valor único, dividir de volta entre as duas pontas seria
   chute. Então R$ digitado no item é convertido no percentual equivalente da
   contribuição daquele item ao primeiro ano, e esse percentual cai igual nos
   dois. A proporção entre setup e mensal é preservada por construção.
   A exceção: no recorrente, desde 23/09/2026, R$ no item é R$ POR MÊS e sai
   só da mensalidade — ver `por_mes`.

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

def eh_incluso(item) -> bool:
    """Esta linha entra na proposta mas NÃO é cobrada — vem junto no pacote.

    POR QUE ISTO EXISTE. Em 18/09/2026, medindo a Prime (conta 34): de 217 linhas
    de orçamento, **122 têm desconto de 100%** e exatamente UMA tem desconto
    percentual de verdade. Não é desconto — é o vendedor dizendo "isto vem junto
    no Pacote Prime" com a única ferramenta que a tela oferecia. No orçamento nº
    23 são 10 das 11 linhas: mesas, vasos, poltronas, cozinha, climatizadores,
    gerador, utensílios, leds, freezer.

    O preço disso era a tela mentir: o resumo anunciava "Economia de R$ 14.850"
    numa proposta onde ninguém descontou nada, e o funil guardava R$ 22.900 de
    bruto pra um negócio de R$ 8.050.

    ESTA FUNÇÃO NÃO MUDA DINHEIRO NENHUM. Cem por cento de desconto já zera a
    linha em `liquido_do_item`, e continua zerando. O que ela faz é LER a
    intenção, pra a tela e a folha pararem de chamar isso de desconto.

    Dois jeitos de dizer a mesma coisa, de propósito:

    * `incluso: true` — o que a tela nova grava;
    * `desc_tipo='pct'` com `desc_val >= 100` — o que as 122 linhas já gravadas
      dizem, e o que uma tela antiga continuaria gravando.

    A tela nova grava OS DOIS. Assim o dinheiro sai igual em qualquer leitor —
    inclusive num deploy antigo que não conheça a marca — e a intenção viaja
    junto. É o mesmo cuidado do `unitario`, que conviveu com o `setup` das
    propostas anteriores a ele.
    """
    if not isinstance(item, dict):
        return False
    if item.get("incluso"):
        return True
    return _tipo(item.get("desc_tipo")) == "pct" and _pct(item.get("desc_val")) >= 100


def contribuicao(item) -> int:
    """O que esta linha soma ao primeiro ano, em centavos — a base do desconto dela.

    Evento: só o valor da linha (qtd × unitário já vem somado em `setup`).
    Recorrente: setup + mensalidade × 12, que é como a tela monta o `ano1`.
    """
    return max(0, _int(item.get("setup")) * 100) + \
        max(0, _int(item.get("mensal")) * 100) * MESES_ANO1


def por_mes(item) -> bool:
    """O desconto em R$ desta linha é POR MÊS — tira da mensalidade, todo mês.

    Recorrente, desde 23/09/2026. Na proposta da HLED (ZAQ) o dono digitou R$ 500
    de desconto numa mensalidade de R$ 1.500 querendo R$ 1.000 por mês; a regra
    antiga (R$ = fatia do primeiro ano) tirou R$ 41,67 por mês e a proposta saiu
    com R$ 5.474,99 no lugar de R$ 3.000. Perguntado, o dono confirmou: no
    recorrente, R$ de desconto é por mês.

    É uma MARCA no item (`desc_mes`), não um tipo novo, de propósito: um leitor
    que não conheça a marca lê o item como `valor` à moda antiga — desconto menor,
    nunca uma linha zerada. E item gravado antes dela segue valendo o que valia,
    até alguém abrir e salvar a proposta de novo (a tela do recorrente grava a
    marca sempre que o desconto é em R$).

    O desconto por mês não toca a implantação: quem quer descontar as duas pontas
    juntas usa o %, que continua caindo igual nas duas."""
    return (isinstance(item, dict) and bool(item.get("desc_mes"))
            and _tipo(item.get("desc_tipo")) == "valor")


def percentual_do_item(item) -> float:
    """O desconto da linha COMO PERCENTUAL, sempre — mesmo quando foi digitado em
    reais. É a conversão que permite descontar setup e mensalidade na mesma
    proporção, e é o motivo de o financeiro continuar sabendo separar as duas."""
    base = contribuicao(item)
    if base <= 0:
        return 0.0
    if _tipo(item.get("desc_tipo")) == "pct":
        return min(100.0, max(0.0, _pct(item.get("desc_val"))))
    if por_mes(item):
        # o equivalente, só pra quem quiser ler um percentual: a conta de verdade
        # do por-mês está em `liquido_do_item`
        mensal = max(0, _int(item.get("mensal"))) * 100
        reais = min(mensal, max(0, _int(item.get("desc_val"))) * 100) * MESES_ANO1
        return min(100.0, 100.0 * reais / base)
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
    if por_mes(item):
        # R$ por mês: sai inteiro da mensalidade (nunca abaixo de zero) e a
        # implantação fica cheia — ver `por_mes`.
        setup_liq = setup
        mensal_liq = max(0, mensal - max(0, _int(item.get("desc_val"))) * 100)
    else:
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
    # o que está na proposta SEM ser cobrado, pelo valor de tabela. Sai separado
    # pra o resumo poder dizer "9 itens inclusos, R$ 13.850 de tabela" em vez de
    # chamar aquilo de "economia" — ver `eh_incluso`. Nenhum total muda por causa
    # disto: a linha incluída já entra líquida em zero, como sempre entrou.
    inc_s = inc_m = 0
    n_inc = 0
    for it in (itens or []):
        if not isinstance(it, dict):
            continue
        b_s += max(0, _int(it.get("setup"))) * 100
        b_m += max(0, _int(it.get("mensal"))) * 100
        liq = liquido_do_item(it)
        l_s += liq["setup"]
        l_m += liq["mensal"]
        if eh_incluso(it):
            n_inc += 1
            inc_s += max(0, _int(it.get("setup"))) * 100
            inc_m += max(0, _int(it.get("mensal"))) * 100
    return {"bruto_setup": b_s, "bruto_mensal": b_m,
            "setup": l_s, "mensal": l_m,
            "desconto_setup": b_s - l_s, "desconto_mensal": b_m - l_m,
            "desconto": (b_s - l_s) + (b_m - l_m) * MESES_ANO1,
            "inclusos": n_inc, "incluso_setup": inc_s, "incluso_mensal": inc_m,
            "incluso": inc_s + inc_m * MESES_ANO1,
            # o desconto que é desconto DE VERDADE: o que sobra depois de tirar o
            # que só está incluso. É este número que pode ir pra tela com a
            # palavra "desconto" sem mentir.
            "desconto_real": max(0, ((b_s - l_s) + (b_m - l_m) * MESES_ANO1)
                                 - (inc_s + inc_m * MESES_ANO1))}


# --------------------------------------------------------------- total do doc

def totais(itens, *, tipo="pct", pct=0, valor=0,
           extra_setup: int = 0, extra_mensal: int = 0,
           fator_mensal: float = 1.0) -> dict:
    """A conta inteira, na ordem que a tela mostra.

    `extra_setup`/`extra_mensal` (centavos) são o que o modo recorrente soma fora
    das linhas — infraestrutura, integrações, canais, suporte dedicado. Eles
    entram no subtotal e por isso RECEBEM o desconto final, mas não recebem
    desconto por item (não são item).

    Devolve tudo em centavos, incluindo as pontas separadas: é delas que saem os
    títulos de setup e de mensalidade.

    `fator_mensal` é o PAGAMENTO ANUAL do recorrente (0.85 = -15% na mensalidade),
    aplicado depois do desconto de cada linha e ANTES do desconto no total — a
    mesma ordem da tela (`calc()` em web/painel_servicos). Sem ele o servidor
    gravava um "Total 1º ano" que ignorava o anual: a tela dizia R$ 29.420 e o
    banco R$ 33.200 (medido em 23/09/2026).
    """
    it = somar_itens(itens)
    setup = it["setup"] + max(0, _int(extra_setup))
    mensal = it["mensal"] + max(0, _int(extra_mensal))
    if fator_mensal != 1.0:
        mensal = int(round(mensal * float(fator_mensal)))
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


# ------------------------------------------------------ as duas formas do recorrente

# O "anual à vista" do recorrente: -15% na mensalidade, o ano pago de uma vez. É o
# mesmo 0.85 do botão da tela e do salvar (web/painel_servicos).
FATOR_ANUAL = 0.85


def formas_recorrente(itens, *, setup_centavos=0, mensal_centavos=0, anual=False,
                      tipo="pct", pct=0, valor=0) -> dict:
    """As DUAS formas de pagar a mesma proposta recorrente, refeitas da linha
    gravada: mensal e anual à vista.

    Por que existe: desde 23/09/2026 a folha mostra as duas lado a lado e é o
    CLIENTE quem escolhe, ao aprovar (pedido do dono, olhando a proposta da HLED).
    A linha do orçamento só guarda a forma que o vendedor marcou — então a outra
    precisa ser refeita, e refeita pela MESMA conta do salvar (`totais`, na mesma
    ordem: linha, anual, total), senão a folha anunciaria um número e o
    financeiro cobraria outro.

    `setup_centavos`/`mensal_centavos` são as colunas do orçamento: o BRUTO, e a
    mensalidade bruta JÁ com o anual quando `anual` (é assim que o salvar grava,
    pro funil e o cockpit lerem a coluna como sempre). O que não é linha
    (infraestrutura, integrações) sai da diferença entre elas e os itens, como
    no salvar. `valor` é o desconto do total em CENTAVOS.

    Devolve, pra cada forma: `setup` e `mensal` líquidos, `ano1`, e `total_anual`
    (as doze mensalidades), mais o que o cliente economiza no anual.
    """
    lista = [it for it in (itens or []) if isinstance(it, dict)]
    itens_setup = sum(max(0, _int(i.get("setup"))) for i in lista) * 100
    itens_mensal = sum(max(0, _int(i.get("mensal"))) for i in lista) * 100
    mensal_cheio = max(0, _int(mensal_centavos))
    if anual:
        mensal_cheio = int(round(mensal_cheio / FATOR_ANUAL))
    extra_setup = max(0, max(0, _int(setup_centavos)) - itens_setup)
    extra_mensal = max(0, mensal_cheio - itens_mensal)
    out = {}
    for nome, fator in (("mensal", 1.0), ("anual", FATOR_ANUAL)):
        t = totais(lista, tipo=tipo, pct=pct, valor=valor, extra_setup=extra_setup,
                   extra_mensal=extra_mensal, fator_mensal=fator)
        out[nome] = {"setup": t["setup"], "mensal": t["mensal"], "ano1": t["total"],
                     "total_anual": t["mensal"] * MESES_ANO1}
    out["economia_anual"] = max(0, out["mensal"]["total_anual"] - out["anual"]["total_anual"])
    # o bruto das mensalidades (tabela) e o que os descontos de linha tiram por mês
    it = somar_itens(lista)
    out["tabela_mensal"] = it["bruto_mensal"] + extra_mensal
    out["tabela_setup"] = it["bruto_setup"] + extra_setup
    out["desconto_itens_mensal"] = it["bruto_mensal"] - it["mensal"]
    out["desconto_itens_setup"] = it["bruto_setup"] - it["setup"]
    return out
