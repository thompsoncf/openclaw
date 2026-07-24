"""Motor de cálculo do Módulo Doméstica (eSocial Doméstico) — CONFERÊNCIA.

Empregador doméstico é PESSOA FÍSICA e o eSocial Doméstico **não tem API**: o
fechamento é sempre no portal gov.br. Este motor NÃO transmite nada — ele
**espelha o cálculo** pra: antecipar a DAE, conferir a guia emitida, gerar o
recibo e cair no livro-caixa PESSOAL do dono da casa.

Cobre qualquer "empregado doméstico" da LC 150/2015 (babá, caseiro, cuidador,
motorista particular, cozinheira, jardineiro, diarista mensalista, doméstica) —
muda só o `cargo`; o cálculo é o mesmo.

Regras não-óbvias (validadas centavo a centavo contra demonstrativo real do
eSocial, competência 11/2025, 2 empregadas — ver o bloco de autoverificação):

- INSS do EMPREGADO é PROGRESSIVO por faixa (cada alíquota só sobre o trecho).
- O DESCONTO SIMPLIFICADO do IRRF **substitui** a dedução (não soma ao INSS):
  base IRRF = remuneração − max(INSS, desconto simplificado).
- O ADIANTAMENTO do 13º entra na base do FGTS, mas NÃO na do INSS/IRRF.
- Encargos patronais são **TRUNCADOS** em 2 casas (67,776 → 67,77), nunca
  arredondados. O INSS do empregado é arredondado (padrão eSocial).
- Tabelas versionadas por ANO em `TABELAS` — nunca cravar valor fora dali.

Dinheiro sempre em CENTAVOS (int). Só stdlib — este arquivo NÃO importa nada do
repo (mesma disciplina do handoff: se algo não encaixar, reporte, não reescreva;
a autoverificação é a prova de que o cálculo está certo).

Autoverificação:  python -m finance.domestica
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date

# ── Alíquotas estatutárias (iguais em 2025 e 2026; base do Simples Doméstico) ──
FGTS_PCT = 0.08               # FGTS mensal (empregador)
FGTS_COMPENSATORIO_PCT = 0.032  # multa rescisória antecipada (empregador)
INSS_PATRONAL_PCT = 0.08      # contribuição previdenciária patronal
GILRAT_PCT = 0.008            # seguro acidente de trabalho (empregador)

# Provisão mensal: 13º (1/12) + férias (1/12) + 1/3 constitucional (1/36) da
# remuneração = 7/36; sobre isso incidem FGTS (8%+3,2%) e INSS patronal+GILRAT
# (8%+0,8%) → fator 1,20. É o "custo real" que o desembolso do mês esconde.
PROVISAO_FRACAO = 7 / 36
PROVISAO_FATOR_ENCARGOS = 1.0 + (FGTS_PCT + FGTS_COMPENSATORIO_PCT) \
    + (INSS_PATRONAL_PCT + GILRAT_PCT)   # = 1.20


# ── Tabelas por ano (INSS empregado progressivo, IRRF, mínimo, simplificado) ──
# Faixas em centavos: (limite_superior, alíquota[, parcela_a_deduzir]).
TABELAS = {
    2025: {
        "salario_minimo": 151800,           # R$ 1.518,00
        "irrf_desconto_simplificado": 60720,  # R$ 607,20
        "inss_faixas": (
            (151800, 0.075),   # até 1.518,00
            (279388, 0.090),   # 1.518,01–2.793,88
            (419083, 0.120),   # 2.793,89–4.190,83
            (815741, 0.140),   # 4.190,84–8.157,41 (teto)
        ),
        "irrf_faixas": (
            (225920, 0.000,     0),   # até 2.259,20 → isento
            (282665, 0.075, 16944),   # 2.259,21–2.826,65
            (375105, 0.150, 38144),   # 2.826,66–3.751,05
            (466468, 0.225, 66277),   # 3.751,06–4.664,68
        ),
        "irrf_ultima": (0.275, 89600),        # acima de 4.664,68
        "irrf_redutor": None,
    },
    2026: {
        # ATENÇÃO: 2026 ainda NÃO foi batido contra DAE real (pendência aberta).
        # INSS/mínimo já reajustados; o IRRF traz o redutor da Lei 15.270/2025
        # (isenta até R$ 5.000, decresce até R$ 7.350). Validar antes de produto.
        "salario_minimo": 162100,           # R$ 1.621,00
        "irrf_desconto_simplificado": 60720,  # placeholder — confirmar 2026
        "inss_faixas": (
            (162100, 0.075),   # até 1.621,00
            (290284, 0.090),   # 1.621,01–2.902,84
            (435427, 0.120),   # 2.902,85–4.354,27
            (847555, 0.140),   # 4.354,28–8.475,55 (teto)
        ),
        "irrf_faixas": (
            (225920, 0.000,     0),
            (282665, 0.075, 16944),
            (375105, 0.150, 38144),
            (466468, 0.225, 66277),
        ),
        "irrf_ultima": (0.275, 89600),
        # Redutor da Lei 15.270/2025: isenção efetiva até 5.000; entre 5.000 e
        # 7.350 o imposto é reduzido linearmente; acima de 7.350, sem redutor.
        "irrf_redutor": {"piso": 500000, "teto": 735000},
    },
}

ANO_PADRAO = 2025


def _tabela(ano: int) -> dict:
    """Tabela do ano; cai no ano mais recente conhecido se o ano for futuro."""
    if ano in TABELAS:
        return TABELAS[ano]
    conhecidos = sorted(TABELAS)
    return TABELAS[conhecidos[-1] if ano > conhecidos[-1] else conhecidos[0]]


def _trunc2(valor_reais: float) -> int:
    """Trunca (NÃO arredonda) pra 2 casas e devolve centavos. Regra dos encargos
    patronais do eSocial: 67,776 → 67,77 (6.777 centavos), nunca 67,78."""
    return int(valor_reais * 100 + 1e-9)  # +epsilon absorve ruído de float


def _round2(valor_reais: float) -> int:
    """Arredonda pra 2 casas e devolve centavos (padrão do INSS do empregado)."""
    return int(round(valor_reais * 100))


# ─────────────────────────────────────────────────────────────────────────
# Entradas
# ─────────────────────────────────────────────────────────────────────────
@dataclass
class Vinculo:
    """Empregado doméstico (qualquer cargo da LC 150/2015)."""
    nome: str
    salario_centavos: int
    cpf: str = ""
    cargo: str = ""            # babá, caseiro, cuidador, motorista... (livre)
    matricula: str = ""
    admitido_em: str = ""      # dd/mm/aaaa (informativo no recibo)


@dataclass
class Lancamentos:
    """Variáveis do mês. Só `extras` e `adiantamento_13` foram validados contra
    o demonstrativo real; os demais seguem a regra legal usual (documentada)."""
    extras_centavos: int = 0            # horas-extra/adicionais (base salarial)
    adiantamento_13_centavos: int = 0   # 1ª parcela do 13º (SÓ na base do FGTS)
    faltas_centavos: int = 0            # desconto de faltas (reduz a remuneração)
    outros_descontos_centavos: int = 0  # descontos diversos (saem do líquido)
    beneficios_centavos: int = 0        # custo do empregador; não entra em base


# ─────────────────────────────────────────────────────────────────────────
# Blocos de cálculo (puros)
# ─────────────────────────────────────────────────────────────────────────
def inss_empregado_centavos(base_centavos: int, ano: int = ANO_PADRAO) -> int:
    """INSS do empregado, PROGRESSIVO por faixa. Cada alíquota incide só sobre o
    trecho dentro da sua faixa; acima do teto trava no teto. Arredondado."""
    faixas = _tabela(ano)["inss_faixas"]
    base = min(max(0, int(base_centavos)), faixas[-1][0])
    total = 0.0
    piso = 0
    for limite, aliq in faixas:
        if base <= piso:
            break
        total += (min(base, limite) - piso) * aliq
        piso = limite
    return _round2(total / 100)


def irrf_centavos(base_apos_deducao_centavos: int, ano: int = ANO_PADRAO) -> int:
    """IRRF mensal sobre a base já líquida da dedução. Aplica a faixa e, em 2026+,
    o redutor da Lei 15.270/2025. Informativo/conferência (dependentes e outras
    deduções seguem no portal)."""
    tab = _tabela(ano)
    base = max(0, int(base_apos_deducao_centavos))
    aliq, deduzir = tab["irrf_ultima"]
    for limite, a, d in tab["irrf_faixas"]:
        if base <= limite:
            aliq, deduzir = a, d
            break
    imposto = max(0, _round2(base * aliq / 100) - deduzir)
    red = tab.get("irrf_redutor")
    if red and imposto > 0:
        if base <= red["piso"]:
            imposto = 0
        elif base < red["teto"]:
            fracao = (base - red["piso"]) / (red["teto"] - red["piso"])
            imposto = int(round(imposto * fracao))
    return imposto


def _dae_vencimento(competencia: date) -> date:
    """DAE vence dia 20 do mês seguinte à competência."""
    ano, mes = competencia.year, competencia.month
    return date(ano + 1, 1, 20) if mes == 12 else date(ano, mes + 1, 20)


# ─────────────────────────────────────────────────────────────────────────
# Cálculo por empregado
# ─────────────────────────────────────────────────────────────────────────
def calcular_empregado(vinculo: Vinculo, lanc: Lancamentos | None = None,
                       ano: int = ANO_PADRAO,
                       competencia: date | None = None) -> dict:
    """Calcula a competência de UM empregado: bases, cada tributo da DAE, líquido
    do recibo, DAE prevista, provisões e o custo real. Tudo em centavos."""
    lanc = lanc or Lancamentos()
    tab = _tabela(ano)
    comp = competencia or date(date.today().year, date.today().month, 1)

    # Remuneração = salário + extras − faltas. É a base de INSS/patronal/GILRAT
    # e o ponto de partida do IRRF. O adiantamento do 13º NÃO entra aqui.
    remuneracao = max(0, vinculo.salario_centavos
                      + lanc.extras_centavos - lanc.faltas_centavos)
    base_fgts = remuneracao + lanc.adiantamento_13_centavos   # 13º entra no FGTS

    # Desconto do empregado
    inss_emp = inss_empregado_centavos(remuneracao, ano)
    # IRRF: a dedução é a MAIOR entre INSS e desconto simplificado (o simplificado
    # substitui, não soma). Base = remuneração − dedução.
    deducao_irrf = max(inss_emp, tab["irrf_desconto_simplificado"])
    base_irrf = max(0, remuneracao - deducao_irrf)
    irrf = irrf_centavos(base_irrf, ano)

    # Encargos patronais (TRUNCADOS em 2 casas — regra do eSocial)
    fgts_mensal = _trunc2(base_fgts * FGTS_PCT / 100)
    fgts_compensatorio = _trunc2(base_fgts * FGTS_COMPENSATORIO_PCT / 100)
    inss_patronal = _trunc2(remuneracao * INSS_PATRONAL_PCT / 100)
    gilrat = _trunc2(remuneracao * GILRAT_PCT / 100)

    # DAE: guia única do eSocial (FGTS + INSS patronal/empregado + GILRAT + IRRF)
    dae = (fgts_mensal + fgts_compensatorio + inss_patronal + gilrat
           + inss_emp + irrf)

    # Recibo de salário (o adiantamento do 13º é recibo à parte, pago além)
    liquido = remuneracao - inss_emp - irrf - lanc.outros_descontos_centavos

    # Provisões (13º + férias + 1/3 com encargos) — TRUNCADAS por empregado
    provisoes = _trunc2(
        remuneracao * PROVISAO_FRACAO * PROVISAO_FATOR_ENCARGOS / 100)

    # Desembolso do mês = líquido + adiantamento 13º + DAE. Custo real soma as
    # provisões (o que o dono da casa só sente em dezembro/nas férias).
    desembolso = liquido + lanc.adiantamento_13_centavos + dae
    custo_real = desembolso + provisoes

    return {
        "nome": vinculo.nome, "cargo": vinculo.cargo,
        "matricula": vinculo.matricula, "competencia": comp,
        "vencimento_dae": _dae_vencimento(comp), "tabela_ano": ano,
        "salario_centavos": vinculo.salario_centavos,
        "extras_centavos": lanc.extras_centavos,
        "adiantamento_13_centavos": lanc.adiantamento_13_centavos,
        "remuneracao_centavos": remuneracao,
        "base_inss_centavos": remuneracao,
        "base_fgts_centavos": base_fgts,
        "base_irrf_centavos": base_irrf,
        "inss_empregado_centavos": inss_emp,
        "inss_patronal_centavos": inss_patronal,
        "gilrat_centavos": gilrat,
        "fgts_mensal_centavos": fgts_mensal,
        "fgts_compensatorio_centavos": fgts_compensatorio,
        "irrf_centavos": irrf,
        "dae_centavos": dae,
        "liquido_centavos": liquido,
        "beneficios_centavos": lanc.beneficios_centavos,
        "provisoes_centavos": provisoes,
        "desembolso_centavos": desembolso,
        "custo_real_centavos": custo_real,
        "abaixo_do_minimo": remuneracao < tab["salario_minimo"],
    }


# ─────────────────────────────────────────────────────────────────────────
# Cálculo da folha (vários empregados)
# ─────────────────────────────────────────────────────────────────────────
def calcular_folha(itens: list[tuple[Vinculo, Lancamentos]] | list[Vinculo],
                   ano: int = ANO_PADRAO,
                   competencia: date | None = None) -> dict:
    """Folha da competência. Aceita lista de (Vinculo, Lancamentos) ou só de
    Vinculo. Devolve os empregados calculados + os totais da casa."""
    calc = []
    for it in itens:
        v, l = it if isinstance(it, tuple) else (it, Lancamentos())
        calc.append(calcular_empregado(v, l, ano, competencia))

    def soma(chave: str) -> int:
        return sum(e[chave] for e in calc)

    return {
        "ano": ano,
        "competencia": (competencia
                        or (calc[0]["competencia"] if calc else None)),
        "empregados": calc,
        "n_empregados": len(calc),
        "dae_total_centavos": soma("dae_centavos"),
        "liquido_total_centavos": soma("liquido_centavos"),
        "provisoes_total_centavos": soma("provisoes_centavos"),
        "desembolso_total_centavos": soma("desembolso_centavos"),
        "custo_real_total_centavos": soma("custo_real_centavos"),
    }


# ─────────────────────────────────────────────────────────────────────────
# Ponte pro livro-caixa (PESSOAL — empregador é PF)
# ─────────────────────────────────────────────────────────────────────────
def lancamentos_livro_caixa(folha: dict) -> list[dict]:
    """Traduz a folha em lançamentos de CAIXA (natureza='pessoal') pro livro do
    dono da casa: líquido, adiantamento do 13º e a DAE. Provisões NÃO entram
    aqui (não são caixa) — vão pro DRE/raio-x pela chave `custo_real`.

    Cada item: {tipo, categoria, natureza, valor_centavos, descricao}.
    """
    comp = folha.get("competencia")
    rot = comp.strftime("%m/%Y") if isinstance(comp, date) else ""
    out: list[dict] = []
    for e in folha["empregados"]:
        quem = e["nome"]
        if e["liquido_centavos"] > 0:
            out.append({"tipo": "despesa", "categoria": "Doméstica",
                        "natureza": "pessoal",
                        "valor_centavos": e["liquido_centavos"],
                        "descricao": f"Salário doméstico {rot} — {quem}"})
        if e["adiantamento_13_centavos"] > 0:
            out.append({"tipo": "despesa", "categoria": "Doméstica",
                        "natureza": "pessoal",
                        "valor_centavos": e["adiantamento_13_centavos"],
                        "descricao": f"Adiantamento 13º {rot} — {quem}"})
        if e["dae_centavos"] > 0:
            out.append({"tipo": "despesa", "categoria": "Doméstica",
                        "natureza": "pessoal",
                        "valor_centavos": e["dae_centavos"],
                        "descricao": f"DAE eSocial {rot} — {quem}"})
    return out


# ─────────────────────────────────────────────────────────────────────────
# Resumo em texto (pro bot / preview da DAE)
# ─────────────────────────────────────────────────────────────────────────
def _brl(centavos: int) -> str:
    s = f"{centavos / 100:,.2f}"
    return "R$ " + s.replace(",", "X").replace(".", ",").replace("X", ".")


def texto_resumo(folha: dict) -> str:
    """Resumo humano da folha: DAE prevista por empregado, totais e o custo real
    com provisões. Deixa claro que é CONFERÊNCIA — o fechamento é no portal."""
    linhas = []
    comp = folha.get("competencia")
    rot = comp.strftime("%m/%Y") if isinstance(comp, date) else ""
    linhas.append(f"📋 Folha doméstica {rot} (conferência)")
    for e in folha["empregados"]:
        venc = e["vencimento_dae"].strftime("%d/%m/%Y")
        linhas.append(
            f"\n• {e['nome']} — líquido {_brl(e['liquido_centavos'])}"
            f"\n  DAE prevista {_brl(e['dae_centavos'])} (vence {venc})")
        if e["abaixo_do_minimo"]:
            linhas.append("  ⚠️ remuneração abaixo do salário mínimo")
    linhas.append(
        f"\n— DAE total: {_brl(folha['dae_total_centavos'])}"
        f"\n— Desembolso do mês: {_brl(folha['desembolso_total_centavos'])}"
        f"\n— Custo real c/ provisões: {_brl(folha['custo_real_total_centavos'])}")
    linhas.append(
        "\nCálculo de conferência — o fechamento continua no portal gov.br "
        "(eSocial Doméstico).")
    return "\n".join(linhas)


# ─────────────────────────────────────────────────────────────────────────
# Autoverificação — bate CENTAVO A CENTAVO com demonstrativo real do eSocial
# (VIA DO EMPREGADOR, competência Novembro/2025, empregador RENATA E. P. T.,
#  2 empregadas: MARILENE ED002 e ISABEL ED003). A prova de que o motor calcula
# certo é este bloco passar inteiro.
# ─────────────────────────────────────────────────────────────────────────
def _autoverificar() -> int:
    comp = date(2025, 11, 1)
    checks: list[tuple[str, int, int]] = []

    def ck(nome, obtido, esperado):
        checks.append((nome, obtido, esperado))

    # ── Empregada 1: MARILENE — salário 1.412,00, adiant. 13º 706,00, sem extra
    m = calcular_empregado(
        Vinculo("MARILENE DE SOUSA OLIVEIRA", 141200, matricula="ED002"),
        Lancamentos(adiantamento_13_centavos=70600), 2025, comp)
    ck("M base INSS",            m["base_inss_centavos"],          141200)
    ck("M base FGTS",            m["base_fgts_centavos"],          211800)
    ck("M base IRRF",            m["base_irrf_centavos"],           80480)
    ck("M INSS empregado 7,5%",  m["inss_empregado_centavos"],      10590)
    ck("M INSS patronal 8%",     m["inss_patronal_centavos"],       11296)
    ck("M GILRAT 0,8%",          m["gilrat_centavos"],               1129)
    ck("M FGTS 8%",              m["fgts_mensal_centavos"],         16944)
    ck("M FGTS compens. 3,2%",   m["fgts_compensatorio_centavos"],   6777)
    ck("M IRRF isento",          m["irrf_centavos"],                    0)
    ck("M DAE total",            m["dae_centavos"],                 46736)
    ck("M líquido recibo",       m["liquido_centavos"],            130610)

    # ── Empregada 2: ISABEL — salário 1.500 + 500 extra noturna, adiant. 750
    i = calcular_empregado(
        Vinculo("ISABEL CRISTINA OLIVEIRA LEMOS", 150000, matricula="ED003"),
        Lancamentos(extras_centavos=50000, adiantamento_13_centavos=75000),
        2025, comp)
    ck("I base INSS",            i["base_inss_centavos"],          200000)
    ck("I base FGTS",            i["base_fgts_centavos"],          275000)
    ck("I base IRRF",            i["base_irrf_centavos"],          139280)
    ck("I INSS progressivo",     i["inss_empregado_centavos"],      15723)
    ck("I INSS patronal 8%",     i["inss_patronal_centavos"],       16000)
    ck("I GILRAT 0,8%",          i["gilrat_centavos"],               1600)
    ck("I FGTS 8%",              i["fgts_mensal_centavos"],         22000)
    ck("I FGTS compens. 3,2%",   i["fgts_compensatorio_centavos"],   8800)
    ck("I IRRF isento",          i["irrf_centavos"],                    0)
    ck("I DAE total",            i["dae_centavos"],                 64123)
    ck("I líquido recibo",       i["liquido_centavos"],            184277)

    # ── Folha da casa (as duas juntas): totais, desembolso e custo real
    folha = calcular_folha(
        [(Vinculo("MARILENE DE SOUSA OLIVEIRA", 141200),
          Lancamentos(adiantamento_13_centavos=70600)),
         (Vinculo("ISABEL CRISTINA OLIVEIRA LEMOS", 150000),
          Lancamentos(extras_centavos=50000, adiantamento_13_centavos=75000))],
        2025, comp)
    ck("Folha DAE total",        folha["dae_total_centavos"],      110859)
    ck("Folha desembolso",       folha["desembolso_total_centavos"], 571346)
    ck("Folha provisões",        folha["provisoes_total_centavos"],  79612)
    ck("Folha custo real",       folha["custo_real_total_centavos"], 650958)

    ok = 0
    for nome, obtido, esperado in checks:
        bateu = obtido == esperado
        ok += bateu
        marca = "✓" if bateu else "✗"
        extra = "" if bateu else f"   (obtido {obtido}, esperado {esperado})"
        print(f"  {marca} {nome}: {_brl(obtido)}{extra}")
    print(f"\n{ok}/{len(checks)} checks — "
          + ("TODOS VERDES ✅" if ok == len(checks) else "FALHOU ❌"))
    return 0 if ok == len(checks) else 1


if __name__ == "__main__":
    import sys
    sys.exit(_autoverificar())
