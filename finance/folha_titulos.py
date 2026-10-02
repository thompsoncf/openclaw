"""A folha em DOIS dias — adiantamento e saldo — virando contas a pagar.

Pedido do dono em 02/10/2026: "colocar por exemplo 5 e 20 como saldo salário e
adiantamento salário". Até aqui a folha tinha UM dia de pagamento
(`funcionarios.dia_pagamento`) e o adiantamento era só o botão "+ adiantar", que
lança na hora o que se digitar. Nada lembrava do dia 20, e nada dizia quanto
sobrava pro dia 5.

AS DUAS DECISÕES DO DONO (02/10/2026)

1. O adiantamento é escolhido POR FUNCIONÁRIO: um percentual do salário ou um
   valor fixo, num dia do mês.
2. O saldo cai no 5º DIA ÚTIL do mês seguinte — o prazo da CLT (art. 459, §1º).
   Quem preferir segue com o dia fixo de sempre (`dia_pagamento`).

COMO VIRA DINHEIRO

Com "gerar as contas a pagar" ligado, cada competência ganha até duas contas a
pagar por pessoa — o adiantamento e o saldo —, nascidas AGUARDANDO a liberação
do dono, como toda conta a pagar (a regra da casa, `empresa.criar_titulo`).

* O saldo é o que a FOLHA diz que falta (`folha_do_mes` → `a_pagar`) menos o
  adiantamento ainda em aberto. Lançou um extra, um desconto, deu aumento? O
  saldo acompanha.
* Dar baixa numa delas grava o evento da folha — 'vale' pro adiantamento,
  'pagamento' pro saldo — amarrado ao MESMO lançamento da baixa
  (`registrar_pagamento`). O caixa não lança duas vezes, e o holerite mostra o
  "961 Adiantamento Salarial" sozinho.
* Quitou a folha pelo "pagar ✓"? As contas que sobraram em aberto daquela
  competência são canceladas: nada ali é devido mais, e pagar de novo seria
  dinheiro saindo duas vezes.

O QUE A SINCRONIZAÇÃO RESPEITA

* Conta CANCELADA pelo dono não volta — só se ele mudar a configuração.
* Valor MUDADO À MÃO (o adiantamento menor deste mês) fica como ele deixou:
  `folha_valor_calculado` guarda o que a folha calculou, e a diferença entre os
  dois é a marca da mão dele. Só a trava de nunca pagar mais do que a folha
  deve passa por cima.
* Mês que já passou não ganha conta: ligar hoje começa na competência de hoje
  (`titulos_folha_desde`), e parte que já venceu não nasce — a empresa
  provavelmente já pagou por fora, e conta inventada é pior que conta faltando.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from . import relogio

_log = logging.getLogger(__name__)

PARTES = ("adiantamento", "saldo")
REGRAS_SALDO = ("dia", "quinto_util")

#: Onde a conta cai no plano de contas (132). Sem o plano, a conta nasce sem
#: classificação — a geração não pode falhar por isso.
PLANO_SALARIO = "4.1.01"       # Salários e Ordenados
PLANO_PRO_LABORE = "4.1.03"    # Pró-labore


# ─────────────────────────────────────────────────────────────── calendário
def pascoa(ano: int) -> date:
    """Domingo de Páscoa no calendário gregoriano (Meeus/Jones/Butcher)."""
    a = ano % 19
    b, c = divmod(ano, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    mes, dia = divmod(h + l_ - 7 * m + 114, 31)
    return date(ano, mes, dia + 1)


def feriados_nacionais(ano: int) -> set[date]:
    """Os feriados nacionais do ano. A Sexta-feira da Paixão anda com a Páscoa; o
    20/11 (Consciência Negra) é nacional desde 2024 (Lei 14.759/2023).

    Carnaval e Corpus Christi NÃO entram: são ponto facultativo no país inteiro."""
    fixos = [(1, 1), (4, 21), (5, 1), (9, 7), (10, 12), (11, 2), (11, 15), (12, 25)]
    if ano >= 2024:
        fixos.append((11, 20))
    dias = {date(ano, m, d) for m, d in fixos}
    dias.add(pascoa(ano) - timedelta(days=2))
    return dias


def dia_util(d: date) -> bool:
    """Dia útil pra pagar salário: segunda a SÁBADO, fora feriado nacional.

    O sábado conta: é a regra do Ministério do Trabalho pra contar o 5º dia útil
    (Instrução Normativa SRT nº 1/1989). Feriado estadual e municipal ficam de
    fora porque variam por cidade e o sistema não sabe onde a empresa fica. O erro
    que isso deixa é sempre pro lado seguro: um feriado da cidade não contado faz
    o vencimento calculado cair um dia ANTES do prazo, nunca depois."""
    return d.weekday() != 6 and d not in feriados_nacionais(d.year)


def quinto_dia_util(ano: int, mes: int) -> date:
    """O 5º dia útil do mês — o prazo legal do salário do mês anterior."""
    d, n = date(ano, mes, 1), 0
    while True:
        if dia_util(d):
            n += 1
            if n == 5:
                return d
        d += timedelta(days=1)


def competencia(d: date | None = None) -> date:
    """O 1º dia do mês de `d` — no dia de Brasília quando `d` não vem."""
    d = d or relogio.hoje()
    return date(d.year, d.month, 1)


def mes_seguinte(comp: date) -> date:
    return date(comp.year + 1, 1, 1) if comp.month == 12 else date(comp.year, comp.month + 1, 1)


def mes_anterior(comp: date) -> date:
    return date(comp.year - 1, 12, 1) if comp.month == 1 else date(comp.year, comp.month - 1, 1)


# ────────────────────────────────────────────────────────── a regra da pessoa
def vencimento_adiantamento(cfg: dict, comp: date) -> date | None:
    """O dia do adiantamento DENTRO da competência (o dia 20 de outubro é o
    adiantamento do salário de outubro). Sem dia configurado, sem adiantamento."""
    dia = cfg.get("adiantamento_dia")
    return date(comp.year, comp.month, int(dia)) if dia else None


def vencimento_saldo(cfg: dict, comp: date) -> date:
    """O saldo do salário de um mês cai no mês SEGUINTE: no 5º dia útil, ou no dia
    fixo de sempre (`dia_pagamento`)."""
    prox = mes_seguinte(comp)
    if cfg.get("saldo_regra") == "quinto_util":
        return quinto_dia_util(prox.year, prox.month)
    dia = max(1, min(28, int(cfg.get("dia_pagamento") or 5)))
    return date(prox.year, prox.month, dia)


def valor_adiantamento(cfg: dict, salario_centavos: int) -> int:
    """Quanto adiantar: o valor fixo, ou o percentual do salário que VALE naquela
    competência (o aumento de novembro já muda o adiantamento de novembro)."""
    if not cfg.get("adiantamento_dia"):
        return 0
    if cfg.get("adiantamento_centavos"):
        return int(cfg["adiantamento_centavos"])
    pct = cfg.get("adiantamento_pct")
    if not pct:
        return 0
    v = Decimal(int(salario_centavos or 0)) * Decimal(str(pct)) / Decimal(100)
    return int(v.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def regra_em_texto(cfg: dict) -> dict:
    """As duas frases que a tela mostra — escritas num lugar só, pra que a linha da
    folha e o formulário digam a mesma coisa."""
    adi = ""
    if cfg.get("adiantamento_dia"):
        if cfg.get("adiantamento_centavos"):
            reais = f"{int(cfg['adiantamento_centavos']) / 100:,.2f}"
            quanto = "R$ " + reais.replace(",", "X").replace(".", ",").replace("X", ".") + " fixo"
        else:
            quanto = f"{_pct_txt(cfg.get('adiantamento_pct')) or '0'}% do salário"
        adi = f"todo dia {int(cfg['adiantamento_dia'])} · {quanto}"
    if cfg.get("saldo_regra") == "quinto_util":
        sal = "5º dia útil do mês seguinte"
    else:
        sal = f"dia {int(cfg.get('dia_pagamento') or 5)} do mês seguinte"
    return {"adiantamento": adi, "saldo": sal}


def ler_percentual(texto) -> Decimal | None:
    """'40', '40,5', '40%' -> Decimal; vazio ou lixo -> None."""
    t = str(texto or "").strip().replace("%", "").replace(",", ".")
    if not t:
        return None
    try:
        return Decimal(t)
    except InvalidOperation:
        return None


# ───────────────────────────────────────────────────────── leitura tolerante
_COLS_CFG = ("id, nome, pro_labore, dia_pagamento, ativo, adiantamento_dia, "
             "adiantamento_pct, adiantamento_centavos, saldo_regra, titulos_folha_desde, "
             "demitido_em")


def _cfg_da_linha(r) -> dict:
    return {"id": int(r[0]), "nome": r[1] or "", "pro_labore": bool(r[2]),
            "dia_pagamento": int(r[3] or 5), "ativo": bool(r[4]),
            "adiantamento_dia": int(r[5]) if r[5] else None,
            "adiantamento_pct": r[6], "adiantamento_centavos": int(r[7]) if r[7] else None,
            "saldo_regra": r[8] or "dia", "titulos_folha_desde": r[9],
            "demitido_em": r[10]}


def configuracoes(pool, conta_id: int) -> dict[int, dict]:
    """A regra de pagamento de cada funcionário da conta. Base sem a 482 → {}: a
    folha segue funcionando como sempre, só sem as datas novas."""
    try:
        with pool.connection() as c:
            rows = c.execute(f"select {_COLS_CFG} from funcionarios where conta_id=%s",
                             (conta_id,)).fetchall()
    except Exception as e:  # noqa: BLE001
        _log.warning("folha em dois dias: sem a configuração da conta %s: %s: %s",
                     conta_id, type(e).__name__, e)
        return {}
    return {int(r[0]): _cfg_da_linha(r) for r in rows}


def _titulos_da_competencia(c, conta_id: int, comp: date,
                            funcionario_id: int | None = None) -> dict[int, dict]:
    """{funcionario_id: {parte: {"vivo": linha|None, "cancelado": bool}}}.
    'vivo' é a conta não cancelada (o índice único garante no máximo uma)."""
    sql = """select id, folha_funcionario_id, folha_parte, status, valor_centavos,
                    folha_valor_calculado, vencimento, aprovacao, pago_em
               from titulos
              where conta_id=%s and folha_competencia=%s and folha_parte is not null
                and folha_funcionario_id is not null"""
    args: list = [conta_id, comp]
    if funcionario_id is not None:
        sql += " and folha_funcionario_id=%s"
        args.append(funcionario_id)
    out: dict[int, dict] = {}
    for r in c.execute(sql + " order by id", args).fetchall():
        parte = out.setdefault(int(r[1]), {}).setdefault(r[2], {"vivo": None, "cancelado": False})
        if r[3] == "cancelado":
            parte["cancelado"] = True
        else:
            parte["vivo"] = {"id": int(r[0]), "status": r[3], "valor": int(r[4] or 0),
                             "calculado": (int(r[5]) if r[5] is not None else None),
                             "vencimento": r[6], "aprovacao": r[7], "pago_em": r[8]}
    return out


# ────────────────────────────────────────────────────── a decisão (pura)
def _mexido(vivo: dict) -> bool:
    """O dono mudou o valor à mão: o que está na conta não é o que a folha calculou."""
    return vivo["calculado"] is not None and vivo["valor"] != vivo["calculado"]


def _partes(recalcular) -> set:
    """`True` = as duas partes; senão, as partes nomeadas (lista, tupla ou set)."""
    if recalcular is True:
        return set(PARTES)
    return set(recalcular or ())


def plano(restante: int, salario: int, cfg: dict, comp: date, tits: dict, *,
          elegivel: bool, recalcular=(), hoje: date) -> list[tuple]:
    """O que fazer com as contas de UMA pessoa numa competência. Função pura (sem
    banco), pra regra ser testada caso a caso.

    `restante` é o `a_pagar` da folha: o que ainda se deve daquela competência,
    já descontado tudo que foi pago (adiantamento pago inclusive).
    `elegivel` = a pessoa gera contas nesta competência. Quem não gera mais só
    passa pela TRAVA (nunca pagar mais do que se deve), nunca ganha conta nova.
    `recalcular` = as partes cuja REGRA o dono acabou de mudar ('adiantamento',
    'saldo'; `True` = as duas). Só nelas o valor volta por cima do que foi mexido
    à mão, e só nelas uma parte cancelada pode nascer de novo — salvar o
    formulário pra trocar o dia do saldo não ressuscita o adiantamento que ele
    cancelou. A DATA segue a regra sempre (quem é elegível): nenhuma tela muda o
    vencimento de um título, então não há mão do dono a respeitar ali — e mudar o
    dia de pagamento tem que mover a conta que já está aberta.

    Devolve ações: ('criar', parte, valor, vencimento) · ('atualizar', id, valor,
    calculado, vencimento_ou_None) · ('cancelar', id).
    """
    acoes: list[tuple] = []
    restante = max(int(restante or 0), 0)
    rec = _partes(recalcular)
    rec_a, rec_s = "adiantamento" in rec, "saldo" in rec

    # ── o adiantamento ──
    ta = tits.get("adiantamento") or {}
    va = ta.get("vivo")
    cfg_a = valor_adiantamento(cfg, salario) if elegivel else 0
    venc_a = vencimento_adiantamento(cfg, comp) if elegivel else None
    a_aberto = 0
    if va and va["status"] == "aberto":
        if restante <= 0 or (rec_a and elegivel and not cfg_a):
            acoes.append(("cancelar", va["id"]))
        else:
            if elegivel and cfg_a and (rec_a or not _mexido(va)):
                alvo = calc = min(cfg_a, restante)
            else:
                alvo, calc = min(va["valor"], restante), va["calculado"]
            novo_venc = venc_a if venc_a and venc_a != va["vencimento"] else None
            if alvo != va["valor"] or calc != va["calculado"] or novo_venc:
                acoes.append(("atualizar", va["id"], alvo, calc, novo_venc))
            a_aberto = alvo
    elif (va is None and elegivel and cfg_a and venc_a and venc_a >= hoje
          and (rec_a or not ta.get("cancelado"))):
        alvo = min(cfg_a, restante)
        if alvo > 0:
            acoes.append(("criar", "adiantamento", alvo, venc_a))
            a_aberto = alvo

    # ── o saldo: o que a folha deve menos o adiantamento ainda por pagar ──
    ts = tits.get("saldo") or {}
    vs = ts.get("vivo")
    calc_s = restante - a_aberto
    venc_s = vencimento_saldo(cfg, comp) if elegivel else None
    if vs and vs["status"] == "aberto":
        if calc_s <= 0:
            acoes.append(("cancelar", vs["id"]))
        else:
            if elegivel and (rec_s or not _mexido(vs)):
                alvo = calc = calc_s
            else:
                alvo, calc = min(vs["valor"], calc_s), vs["calculado"]
            novo_venc = venc_s if venc_s and venc_s != vs["vencimento"] else None
            if alvo != vs["valor"] or calc != vs["calculado"] or novo_venc:
                acoes.append(("atualizar", vs["id"], alvo, calc, novo_venc))
    elif (vs is None and elegivel and calc_s > 0 and venc_s and venc_s >= hoje
          and (rec_s or not ts.get("cancelado"))):
        acoes.append(("criar", "saldo", calc_s, venc_s))
    return acoes


def _descricao(parte: str, comp: date, pro_labore: bool) -> str:
    mm = f"{comp.month:02d}/{comp.year}"
    if parte == "adiantamento":
        return f"Adiantamento {'de pró-labore' if pro_labore else 'salarial'} {mm}"
    return f"{'Pró-labore' if pro_labore else 'Salário'} {mm} (saldo)"


# ───────────────────────────────────────────────────────────── a gravação
def sincronizar(pool, conta_id: int, competencias=None, *,
                funcionario_id: int | None = None, recalcular=(),
                hoje: date | None = None) -> dict:
    """Põe as contas a pagar da folha em dia com a folha e a regra de cada pessoa.

    Idempotente: rodar duas vezes seguidas não muda nada na segunda. Sem
    `competencias`, olha o mês de hoje e o anterior (o saldo do mês passado vence
    no começo deste). `recalcular` (ver `plano`) vale só do mês de HOJE em diante:
    mudar a regra hoje não reabre o que o dono decidiu sobre o mês passado.
    Tolerante: base sem a 482, ou qualquer falha, vira log — quem chama (a baixa,
    o "+ adiantar", o cron) nunca quebra por causa disto."""
    from . import empresa as emp
    hoje = hoje or relogio.hoje()
    atual = competencia(hoje)
    if competencias is None:
        competencias = [mes_anterior(atual), atual]
    resumo = {"criados": 0, "atualizados": 0, "cancelados": 0}
    try:
        cfgs = configuracoes(pool, conta_id)
        if not cfgs:
            return resumo
        with pool.connection() as c:
            tem_titulo = c.execute(
                """select 1 from titulos where conta_id=%s and folha_parte is not null
                     and status='aberto' limit 1""", (conta_id,)).fetchone()
        if not tem_titulo and not any(f["titulos_folha_desde"] for f in cfgs.values()):
            return resumo          # ninguém gera e nada aberto: nada a fazer
        planos = {False: emp._plano_por_codigo(pool, conta_id, PLANO_SALARIO),
                  True: emp._plano_por_codigo(pool, conta_id, PLANO_PRO_LABORE)}
        for comp in sorted(set(competencias)):
            folha = emp.folha_do_mes(pool, conta_id, comp.year, comp.month)
            itens = {int(i["id"]): i for i in folha["itens"]}
            with pool.connection() as c:
                tits = _titulos_da_competencia(c, conta_id, comp, funcionario_id)
                alvos = set(tits)
                for fid, cfg in cfgs.items():
                    if cfg["titulos_folha_desde"] and (funcionario_id is None
                                                       or fid == funcionario_id):
                        alvos.add(fid)
                for fid in sorted(alvos):
                    item, cfg = itens.get(fid), cfgs.get(fid)
                    if item is None or cfg is None:
                        continue   # fora da folha deste mês: o dono decide
                    desde = cfg["titulos_folha_desde"]
                    # Demitido não ganha conta nova: a rescisão tem outro prazo (10
                    # dias, CLT art. 477) e outra conta — o dono acerta à mão. O que
                    # já existe segue na trava de nunca pagar mais que o devido.
                    elegivel = bool(cfg["ativo"] and desde and comp >= competencia(desde)
                                    and cfg.get("demitido_em") is None)
                    for acao in plano(item["a_pagar_centavos"], item["salario_centavos"],
                                      cfg, comp, tits.get(fid, {}), elegivel=elegivel,
                                      recalcular=(recalcular if comp >= atual else ()),
                                      hoje=hoje):
                        _aplicar(c, conta_id, fid, cfg, comp, acao,
                                 planos[bool(cfg["pro_labore"])], resumo)
                c.commit()
    except Exception as e:  # noqa: BLE001
        _log.warning("folha em dois dias: não sincronizou a conta %s: %s: %s",
                     conta_id, type(e).__name__, e)
    return resumo


def _aplicar(c, conta_id: int, fid: int, cfg: dict, comp: date, acao: tuple,
             plano_id, resumo: dict) -> None:
    from .empresa import CAT_PESSOAL
    if acao[0] == "criar":
        _, parte, valor, venc = acao
        # Nasce AGUARDANDO, como toda conta a pagar (empresa.criar_titulo). O
        # `on conflict` é a outra metade da idempotência: duas sincronizações ao
        # mesmo tempo (o cron e um clique) não fazem duas contas — o índice
        # único da 482 recusa a segunda, e ela sai calada.
        r = c.execute(
            """insert into titulos
                 (conta_id, tipo, descricao, contraparte, valor_centavos, vencimento,
                  categoria, recorrente, aprovacao, plano_conta_id, tipo_despesa,
                  folha_funcionario_id, folha_competencia, folha_parte,
                  folha_valor_calculado)
               values (%s,'pagar',%s,%s,%s,%s,%s,false,'aguardando',%s,'fixa',
                       %s,%s,%s,%s)
               on conflict do nothing returning id""",
            (conta_id, _descricao(parte, comp, cfg["pro_labore"]), cfg["nome"], valor,
             venc, CAT_PESSOAL, plano_id, fid, comp, parte, valor)).fetchone()
        if r:
            resumo["criados"] += 1
    elif acao[0] == "atualizar":
        _, tid, valor, calc, venc = acao
        cur = c.execute(
            """update titulos
                  set valor_centavos=%s, folha_valor_calculado=%s,
                      vencimento=coalesce(%s, vencimento)
                where id=%s and conta_id=%s and status='aberto'""",
            (valor, calc, venc, tid, conta_id))
        resumo["atualizados"] += cur.rowcount
    elif acao[0] == "cancelar":
        cur = c.execute(
            "update titulos set status='cancelado' where id=%s and conta_id=%s "
            "and status='aberto'", (acao[1], conta_id))
        resumo["cancelados"] += cur.rowcount


def sincronizar_todas(pool, hoje: date | None = None) -> dict:
    """O passo do cron diário: toda conta que gera contas da folha (ou que ainda
    tem alguma aberta). É ele que faz a conta de novembro nascer em 1º/11."""
    try:
        with pool.connection() as c:
            contas = [int(r[0]) for r in c.execute(
                """select conta_id from funcionarios where titulos_folha_desde is not null
                   union
                   select conta_id from titulos
                    where folha_parte is not null and status='aberto'""").fetchall()]
    except Exception as e:  # noqa: BLE001
        _log.warning("folha em dois dias: sem a lista de contas: %s: %s",
                     type(e).__name__, e)
        return {"contas": 0, "criados": 0, "atualizados": 0, "cancelados": 0}
    total = {"contas": len(contas), "criados": 0, "atualizados": 0, "cancelados": 0}
    for cid in sorted(contas):
        r = sincronizar(pool, cid, hoje=hoje)
        for k in ("criados", "atualizados", "cancelados"):
            total[k] += r[k]
    return total


def configurar(pool, conta_id: int, funcionario_id: int, *,
               adiantamento_dia=None, adiantamento_pct=None,
               adiantamento_centavos=None, saldo_regra: str = "dia",
               dia_pagamento: int | None = None, gerar_titulos: bool = False,
               hoje: date | None = None) -> dict:
    """Grava as datas de pagamento de UMA pessoa e reaplica nas contas abertas.

    Sem dia de adiantamento, não há adiantamento (o percentual e o valor são
    apagados juntos). Com dia, é UM dos dois: percentual OU valor fixo — o fixo
    ganha se vierem os dois. Ligar as contas a pagar marca a competência de HOJE
    como a primeira; religar não mexe na data de quem já estava ligado."""
    hoje = hoje or relogio.hoje()
    dia = int(adiantamento_dia) if adiantamento_dia else None
    pct = ler_percentual(adiantamento_pct) if adiantamento_pct not in (None, "") else None
    fixo = int(adiantamento_centavos) if adiantamento_centavos else None
    if dia is None:
        pct = fixo = None
    else:
        if not 1 <= dia <= 28:
            return {"ok": False, "erro": "O dia do adiantamento vai de 1 a 28."}
        if fixo:
            pct = None
        elif pct is None or not (0 < pct < 100):
            return {"ok": False, "erro": "Diga quanto adiantar: um percentual do salário "
                                         "(de 1 a 99) ou um valor fixo."}
    if saldo_regra not in REGRAS_SALDO:
        saldo_regra = "dia"
    if dia_pagamento is not None:
        dia_pagamento = max(1, min(28, int(dia_pagamento)))
    # O que MUDOU, parte por parte: é só nela que o valor mexido à mão volta ao
    # calculado e que uma parte cancelada pode nascer de novo (ver `plano`).
    antes = configuracoes(pool, conta_id).get(int(funcionario_id)) or {}
    mudou = set()
    if (antes.get("adiantamento_dia"), _dec(antes.get("adiantamento_pct")),
            antes.get("adiantamento_centavos")) != (dia, _dec(pct), fixo):
        mudou.add("adiantamento")
    if (antes.get("saldo_regra"), antes.get("dia_pagamento")) != (
            saldo_regra, dia_pagamento or antes.get("dia_pagamento")):
        mudou.add("saldo")
    with pool.connection() as c:
        r = c.execute(
            """update funcionarios
                  set adiantamento_dia=%s, adiantamento_pct=%s, adiantamento_centavos=%s,
                      saldo_regra=%s, dia_pagamento=coalesce(%s, dia_pagamento),
                      titulos_folha_desde = case when %s
                          then coalesce(titulos_folha_desde, %s) else null end
                where id=%s and conta_id=%s returning id""",
            (dia, pct, fixo, saldo_regra, dia_pagamento, bool(gerar_titulos),
             competencia(hoje), funcionario_id, conta_id)).fetchone()
        c.commit()
    if not r:
        return {"ok": False, "erro": "Funcionário não encontrado."}
    res = sincronizar(pool, conta_id, funcionario_id=funcionario_id, recalcular=mudou,
                      hoje=hoje)
    return {"ok": True, **res}


def _dec(v) -> Decimal | None:
    """Percentual comparável: 40, '40', Decimal('40.00') são o mesmo."""
    if v in (None, ""):
        return None
    return Decimal(str(v)).normalize()


def ressincronizar(pool, conta_id: int, funcionario_id: int | None = None,
                   comp: date | None = None) -> None:
    """O gancho das ações da folha (extra, desconto, aumento, "pagar ✓"...): a
    folha mudou, o saldo acompanha. Nunca levanta — a ação já foi gravada.
    Sem `comp`, o mês de hoje e o anterior."""
    try:
        comps = [competencia(comp)] if comp is not None else None
        sincronizar(pool, conta_id, comps, funcionario_id=funcionario_id)
    except Exception as e:  # noqa: BLE001
        _log.warning("folha em dois dias: gancho falhou na conta %s: %s: %s",
                     conta_id, type(e).__name__, e)


# ───────────────────────────────────────────── a baixa vira evento da folha
def _vinculo(c, conta_id: int, titulo_id: int):
    """(parte, funcionario_id, competencia, valor) da conta da folha, ou None. Lido
    por `to_jsonb` pra base sem a 482 seguir dando baixa igual."""
    r = c.execute(
        """select to_jsonb(t)->>'folha_parte', to_jsonb(t)->>'folha_funcionario_id',
                  to_jsonb(t)->>'folha_competencia', t.valor_centavos
             from titulos t where t.id=%s and t.conta_id=%s""",
        (titulo_id, conta_id)).fetchone()
    if not r or not r[0] or not r[1] or not r[2]:
        return None
    return r[0], int(r[1]), date.fromisoformat(r[2]), int(r[3] or 0)


def registrar_pagamento(c, conta_id: int, titulo_id: int, lancamento_id: int) -> int | None:
    """A conta da folha foi paga: vira o evento da folha, na MESMA transação da
    baixa (`c`) e amarrado ao MESMO lançamento — o caixa já lançou; a folha só
    fica sabendo. Conta que não é da folha: não faz nada.

    'vale' pro adiantamento (desconta do líquido, sai no holerite como 961) e
    'pagamento' pro saldo (é o que o "pagar ✓" grava)."""
    v = _vinculo(c, conta_id, titulo_id)
    if v is None or v[3] <= 0:
        return None
    parte, fid, comp, valor = v
    tipo = "vale" if parte == "adiantamento" else "pagamento"
    if c.execute(
            """select 1 from folha_eventos
                where conta_id=%s and funcionario_id=%s and competencia=%s and tipo=%s
                  and lancamento_id=%s""",
            (conta_id, fid, comp, tipo, lancamento_id)).fetchone():
        return None    # já registrado (a baixa é uma só; isto é cinto de segurança)
    r = c.execute(
        """insert into folha_eventos
             (conta_id, funcionario_id, tipo, valor_centavos, competencia, descricao,
              lancamento_id)
           values (%s,%s,%s,%s,%s,%s,%s) returning id""",
        (conta_id, fid, tipo, valor, comp, "pago na conta a pagar", lancamento_id),
    ).fetchone()
    return int(r[0])


def desfazer_pagamento(c, conta_id: int, titulo_id: int, lancamento_id: int) -> int:
    """O inverso, pra quando a conciliação é desfeita: some o evento que ela tinha
    criado na folha (e só ele — o lançamento do extrato continua onde estava)."""
    v = _vinculo(c, conta_id, titulo_id)
    if v is None or not lancamento_id:
        return 0
    parte, fid, comp, _ = v
    tipo = "vale" if parte == "adiantamento" else "pagamento"
    cur = c.execute(
        """delete from folha_eventos
            where conta_id=%s and funcionario_id=%s and competencia=%s and tipo=%s
              and lancamento_id=%s""",
        (conta_id, fid, comp, tipo, lancamento_id))
    return cur.rowcount


# ─────────────────────────────────────────────────────────── pra tela
def agenda(pool, conta_id: int, itens: list[dict], hoje: date | None = None) -> dict[int, dict]:
    """O que a linha de cada pessoa mostra na folha: a regra em texto e as datas
    deste mês, com o valor e a situação de cada parte ('pago', 'aberto' ou
    'previsto' — previsto é o que a regra diz, sem conta a pagar ainda).

    Também traz o SALDO DO MÊS PASSADO enquanto a conta dele estiver aberta: no
    começo do mês é ele o pagamento da vez, e a folha mostra o mês corrente."""
    hoje = hoje or relogio.hoje()
    cfgs = configuracoes(pool, conta_id)
    if not cfgs:
        return {}
    comp = competencia(hoje)
    anterior = mes_anterior(comp)
    try:
        with pool.connection() as c:
            tits = _titulos_da_competencia(c, conta_id, comp)
            tits_ant = _titulos_da_competencia(c, conta_id, anterior)
    except Exception as e:  # noqa: BLE001
        _log.warning("folha em dois dias: sem as contas da conta %s: %s: %s",
                     conta_id, type(e).__name__, e)
        return {}
    out: dict[int, dict] = {}
    for it in itens:
        fid = int(it["id"])
        cfg = cfgs.get(fid)
        if cfg is None:
            continue
        configurado = bool(cfg["adiantamento_dia"] or cfg["saldo_regra"] != "dia"
                           or cfg["titulos_folha_desde"])
        tf = tits.get(fid, {})
        partes = {}
        a_aberto = 0
        # demitido: só as contas que existem — a previsão seria de um salário
        # que a rescisão substitui
        prever = cfg.get("demitido_em") is None
        # parte CANCELADA (pelo dono ou pela folha quitada) não volta como
        # "prevista": ele dispensou, e a tela não pode dizer o contrário
        ta, ts = tf.get("adiantamento") or {}, tf.get("saldo") or {}
        va = ta.get("vivo")
        venc_a = vencimento_adiantamento(cfg, comp)
        if va:
            partes["adiantamento"] = _parte_da_conta(va)
            a_aberto = va["valor"] if va["status"] == "aberto" else 0
        elif prever and not ta.get("cancelado") and venc_a and venc_a >= hoje:
            v = min(valor_adiantamento(cfg, it["salario_centavos"]),
                    max(int(it["a_pagar_centavos"]), 0))
            if v > 0:
                partes["adiantamento"] = {"vencimento": venc_a, "valor": v,
                                          "situacao": "previsto"}
                a_aberto = v
        vs = ts.get("vivo")
        if vs:
            partes["saldo"] = _parte_da_conta(vs)
        elif prever and not ts.get("cancelado"):
            v = max(int(it["a_pagar_centavos"]), 0) - a_aberto
            if v > 0:
                partes["saldo"] = {"vencimento": vencimento_saldo(cfg, comp), "valor": v,
                                   "situacao": "previsto"}
        vs_ant = ((tits_ant.get(fid, {}).get("saldo") or {}).get("vivo"))
        saldo_anterior = (_parte_da_conta(vs_ant)
                          if vs_ant and vs_ant["status"] == "aberto" else None)
        if saldo_anterior:
            saldo_anterior["mes"] = f"{anterior.month:02d}/{anterior.year}"
        out[fid] = {"configurado": configurado, "regra": regra_em_texto(cfg),
                    "gera_titulos": bool(cfg["titulos_folha_desde"]), "cfg": cfg,
                    "pct_txt": _pct_txt(cfg.get("adiantamento_pct")),
                    "partes": partes, "saldo_anterior": saldo_anterior}
    return out


def _pct_txt(pct) -> str:
    """40.00 -> '40'; 40.50 -> '40,5' (o que o formulário mostra de volta)."""
    if pct in (None, ""):
        return ""
    return format(Decimal(str(pct)).normalize(), "f").replace(".", ",")


def _parte_da_conta(v: dict) -> dict:
    situacao = "pago" if v["status"] == "pago" else "aberto"
    return {"vencimento": v["vencimento"], "valor": v["valor"], "situacao": situacao,
            "pago_em": v.get("pago_em"), "titulo_id": v["id"],
            "aprovacao": v.get("aprovacao"), "mexido": _mexido(v)}
