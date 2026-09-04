"""Módulo Empresa (PJ) — Fase 1.

Camada GERAL de empresa (qualquer segmento), separada do nicho Fornecedor:
- Títulos a pagar/receber (a baixa vira lançamento no livro-caixa — fonte única).
- Equipe e folha GERENCIAL (vales, extras, custo real com encargos estimados).
  A folha OFICIAL (eSocial/FGTS/guias) segue com o contador — isto é gestão.
- Fluxo de caixa projetado, DRE simplificado e relatório do contador (CSV).

Gate: modulo_pj_ativo() — automático pra plano PJ (trial/ativa); conta_modulos
funciona como override de cortesia (admin libera piloto sem cobrança).

Multi-tenant sagrado: todo método é escopado por conta_id.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from .livro_caixa import LivroCaixa
from .models import Lancamento, Tipo

_log = logging.getLogger("openclaw.empresa")

# Categorias empresariais usadas nos lançamentos gerados pelo módulo.
CAT_PESSOAL = "Pessoal"
CAT_FORNECEDORES = "Fornecedores"
CAT_VENDAS = "Vendas"

# Custo real GERENCIAL (estimativa p/ Simples Nacional, transparente na tela):
# FGTS 8% + provisão 13º (1/12) + provisão férias com 1/3 ((1/12)*(4/3)).
# INSS patronal não entra (no Simples está dentro do DAS). Pró-labore: fator 1.
FATOR_ENCARGOS = 1.0 + 0.08 + (1.0 / 12.0) + (1.0 / 12.0) * (4.0 / 3.0)

# ── Desconto do EMPREGADO na folha (o que sai do líquido dele) ────────────────
# INSS PROGRESSIVO: cada alíquota incide só sobre a parte do salário DENTRO da
# faixa (igual ao holerite). Faixas em centavos: (limite_superior, alíquota).
# Acima do teto (última faixa) o desconto trava no valor do teto. Ajustar aqui
# quando a tabela do governo mudar (ou o salário mínimo reajustar as faixas).
INSS_FAIXAS = (
    (162100, 0.075),   # até 1.621,00
    (290284, 0.090),   # 1.621,01 até 2.902,84
    (435427, 0.120),   # 2.902,85 até 4.354,27
    (847555, 0.140),   # 4.354,28 até 8.475,55 (teto)
)
INSS_TETO_CENTAVOS = INSS_FAIXAS[-1][0]
# percentual do salário descontado de quem opta por vale-transporte (máx. legal)
VALE_TRANSPORTE_PCT = 0.06


def inss_desconto_centavos(base_centavos: int) -> int:
    """Desconto de INSS do empregado sobre `base_centavos`, PROGRESSIVO por faixa.
    Cada alíquota incide só sobre o trecho do salário dentro da sua faixa; acima
    do teto o desconto para no valor do teto. Retorna centavos (arredondado)."""
    base = max(0, int(base_centavos))
    base = min(base, INSS_TETO_CENTAVOS)   # trava no teto
    total = 0.0
    piso = 0
    for limite, aliq in INSS_FAIXAS:
        if base <= piso:
            break
        trecho = min(base, limite) - piso
        total += trecho * aliq
        piso = limite
    return int(round(total))


def inss_faixa_pct(base_centavos: int) -> str:
    """Alíquota da FAIXA em que o salário cai (a marginal, como o holerite mostra
    na coluna Referência). Ex.: 1.621,00 -> '7,5%'; 1.980,38 -> '9%'."""
    base = min(max(0, int(base_centavos)), INSS_TETO_CENTAVOS)
    piso, aliq = 0, INSS_FAIXAS[0][1]
    for limite, a in INSS_FAIXAS:
        if base > piso:
            aliq = a
        piso = limite
    s = f"{aliq * 100:.1f}".rstrip("0").rstrip(".").replace(".", ",")
    return s + "%"


def vale_transporte_desconto_centavos(salario_centavos: int,
                                      opta: bool = True) -> int:
    """Desconto de vale-transporte: 6% do salário (limite legal do empregado).
    Só desconta quem opta pelo benefício. Retorna centavos (arredondado)."""
    if not opta:
        return 0
    return int(round(max(0, int(salario_centavos)) * VALE_TRANSPORTE_PCT))


# ── FGTS: recolhido pela EMPRESA (não sai do líquido do funcionário) ──────────
FGTS_PCT = 0.08


def fgts_mes_centavos(base_centavos: int) -> int:
    """FGTS do mês: 8% da base (salário + verbas de natureza salarial). É custo
    do EMPREGADOR — informativo no holerite, não desconta do funcionário."""
    return int(round(max(0, int(base_centavos)) * FGTS_PCT))


# ── IRRF: tabela progressiva oficial (informativa no holerite) ────────────────
# Faixas mensais em centavos: (limite_superior, alíquota, parcela_a_deduzir).
# Base = salário + extras − INSS (sem dependentes, que não rastreamos aqui).
# A folha do zaq é GERENCIAL: mostramos a faixa pra referência; a retenção
# oficial (com dependentes/outras deduções) segue com o contador.
IRRF_FAIXAS = (
    (225920, 0.000,      0),   # até 2.259,20 → isento
    (282665, 0.075,  16944),   # 2.259,21–2.826,65
    (375105, 0.150,  38144),   # 2.826,66–3.751,05
    (466468, 0.225,  66277),   # 3.751,06–4.664,68
)
_IRRF_ULTIMA = (0.275, 89600)  # acima de 4.664,68


def irrf_info(base_centavos: int) -> dict:
    """Faixa de IRRF pra `base_centavos` (base = salário + extras − INSS).
    Retorna {aliquota, imposto_centavos, isento}. Informativo — não desconta do
    líquido gerencial (a retenção oficial segue com o contador)."""
    base = max(0, int(base_centavos))
    aliq, deduzir = _IRRF_ULTIMA
    for limite, a, d in IRRF_FAIXAS:
        if base <= limite:
            aliq, deduzir = a, d
            break
    imposto = max(0, int(round(base * aliq)) - deduzir)
    return {"aliquota": aliq, "imposto_centavos": imposto, "isento": imposto <= 0}


# ─────────────────────────────────────────────────────────────────────────
# Gate do módulo
# ─────────────────────────────────────────────────────────────────────────
def modulo_pj_ativo(pool, conta_id: int) -> bool:
    """True se a conta tem o módulo Empresa.

    Automático: plano de tipo_conta='pj' com status trial/ativa.
    Override:   linha ativa em conta_modulos (codigo 'pj') — cortesia/admin.
    """
    with pool.connection() as c:
        r = c.execute(
            """select 1
                 from contas co join planos p on p.codigo = co.plano
                where co.id = %s and p.tipo_conta = 'pj'
                  and co.status in ('trial','ativa')""",
            (conta_id,),
        ).fetchone()
        if r:
            return True
        r = c.execute(
            "select 1 from conta_modulos where conta_id=%s and modulo='pj' and ativo",
            (conta_id,),
        ).fetchone()
    return r is not None


def acesso_pj(pool, conta_id: int) -> bool:
    """Gate das abas do PJ base (Produtos/Servicos/Vendas). Uma conta acessa o PJ
    base se tem o modulo PJ OU se e' fornecedor. Motivo: os produtos vivem no PJ
    base e o marketplace le' de la' — entao ser fornecedor IMPLICA usar o PJ base.
    Ponto unico de decisao: menu e rotas usam esta funcao pra nunca discordarem."""
    if modulo_pj_ativo(pool, conta_id):
        return True
    with pool.connection() as c:
        r = c.execute(
            "select 1 from contas where id=%s and eh_fornecedor",
            (conta_id,),
        ).fetchone()
    return r is not None


# ─────────────────────────────────────────────────────────────────────────
# Títulos (contas a pagar e a receber)
# ─────────────────────────────────────────────────────────────────────────
#: Os três estados da liberação do dono. Ver o cabeçalho da migração 195 pra por
#: que isto NÃO é mais um valor de `status`.
APROVACOES = ("aguardando", "autorizado", "recusado")


def criar_titulo(pool, conta_id: int, tipo: str, descricao: str,
                 valor_centavos: int, vencimento: date,
                 contraparte: str = "", categoria: str = "",
                 recorrente: bool = False,
                 criado_por: int | None = None,
                 cliente_id: int | None = None,
                 precisa_aprovacao: bool | None = None) -> dict:
    """Cria um título aberto. tipo: 'pagar' | 'receber'. cliente_id LIGA o título
    a um cliente da base (honorário/venda a prazo aparece na ficha dele).

    `precisa_aprovacao=None` (o padrão) aplica A REGRA DA CASA: **toda conta a
    PAGAR nasce aguardando liberação**; a receber nasce liberada, porque dinheiro
    entrando ninguém precisa autorizar.

    A regra mudou de lugar em 04/09/2026, e o motivo é o que importa. Ela nascia
    na tela — "aguarda quem não é o dono" —, e a primeira semana no ar mostrou o
    furo: na Prime só o dono acessa o financeiro (os três do time são `vendedor`),
    então nenhum título jamais nascia aguardando e a fila nunca acendia. Perguntado,
    o dono escolheu que **tudo espera, inclusive o que ele mesmo lança**: vira um
    checklist de conferência em vez de um controle de quem gasta.

    Sendo regra do negócio e não de tela, o lugar dela é aqui — senão cada porta
    que cria título (o formulário, o agente do WhatsApp, o PDV) teria a sua cópia,
    e a do agente já estava faltando.

    O parâmetro continua existindo como escape explícito, e tem um usuário real: a
    parcela seguinte de um título recorrente herda a decisão do anterior (ver
    `dar_baixa_titulo`) — o aluguel autorizado em janeiro não volta a perguntar em
    fevereiro."""
    if tipo not in ("pagar", "receber"):
        raise ValueError("tipo deve ser 'pagar' ou 'receber'")
    if not categoria:
        categoria = CAT_FORNECEDORES if tipo == "pagar" else CAT_VENDAS
    cli_id = int(cliente_id) if cliente_id else None
    if precisa_aprovacao is None:
        precisa_aprovacao = (tipo == "pagar")
    aprov = "aguardando" if precisa_aprovacao else "autorizado"
    with pool.connection() as c:
        r = c.execute(
            """insert into titulos
                 (conta_id, tipo, descricao, contraparte, valor_centavos,
                  vencimento, categoria, recorrente, criado_por, cliente_id,
                  aprovacao)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
            (conta_id, tipo, (descricao or "").strip(), (contraparte or "").strip(),
             int(valor_centavos), vencimento, categoria, bool(recorrente),
             criado_por, cli_id, aprov),
        ).fetchone()
        c.commit()
    return {"id": r[0], "tipo": tipo, "descricao": descricao,
            "valor_centavos": int(valor_centavos), "vencimento": vencimento,
            "status": "aberto", "recorrente": bool(recorrente),
            "cliente_id": cli_id, "aprovacao": aprov}


def listar_titulos(pool, conta_id: int, status: str = "aberto",
                   tipo: str | None = None, limite: int = 100) -> list[dict]:
    """Títulos da conta. Abertos vêm por vencimento; pagos, do mais recente."""
    cond = "t.conta_id=%s and t.status=%s"
    args: list = [conta_id, status]
    if tipo in ("pagar", "receber"):
        cond += " and t.tipo=%s"
        args.append(tipo)
    ordem = "t.vencimento asc, t.id asc" if status == "aberto" else "t.pago_em desc, t.id desc"
    with pool.connection() as c:
        rows = c.execute(
            f"""select t.id, t.tipo, t.descricao, t.contraparte, t.valor_centavos,
                       t.vencimento, t.status, t.recorrente, t.categoria,
                       t.cobranca_link_url, t.pago_em, t.lancamento_id,
                       t.cliente_id, coalesce(p.nome, cl.nome) as cliente_nome,
                       t.aprovacao, t.aprovacao_motivo, t.pago_sem_autorizacao,
                       coalesce(nullif(quem.nome,''), quem.email, '') as criado_nome,
                       coalesce(nullif(dono.nome,''), dono.email, '') as aprovado_nome,
                       t.aprovado_em
                  from titulos t
                  left join clientes cl on cl.id = t.cliente_id
                  left join pessoas p on p.id = cl.pessoa_id
                  left join membros quem on quem.id = t.criado_por
                  left join membros dono on dono.id = t.aprovado_por
                 where {cond}
                 order by {ordem} limit %s""",
            (*args, limite),
        ).fetchall()
    hoje = date.today()
    out = []
    for r in rows:
        venc = r[5]
        out.append({
            "id": r[0], "tipo": r[1], "descricao": r[2], "contraparte": r[3],
            "valor_centavos": int(r[4] or 0), "vencimento": venc,
            "status": r[6], "recorrente": bool(r[7]), "categoria": r[8],
            "cobranca_link_url": r[9], "pago_em": r[10], "lancamento_id": r[11],
            "cliente_id": r[12], "cliente_nome": r[13],
            "atrasado": (r[6] == "aberto" and venc is not None and venc < hoje),
            # A LIBERAÇÃO DO DONO — ver migração 195. Vem junto do resto porque a
            # tela precisa das três coisas na mesma linha (dinheiro, prazo,
            # decisão) pra escrever UM selo; buscar em separado seria três
            # verdades chegando em momentos diferentes.
            "aprovacao": r[14] or "autorizado",
            "aprovacao_motivo": r[15],
            "pago_sem_autorizacao": bool(r[16]),
            "criado_nome": r[17], "aprovado_nome": r[18], "aprovado_em": r[19],
            # `sem_fornecedor` é o selo da frente 1: nem texto nem ficha ligada.
            # Sai daqui, e não de um `{% if %}` na tela, pra que o relatório e a
            # tela concordem sobre o que é "sem fornecedor".
            "sem_fornecedor": (r[1] == "pagar" and not (r[3] or "").strip()
                               and not r[12]),
        })
    return out


def resumo_carteira(pool, conta_id: int) -> dict:
    """Carteira de clientes: títulos a RECEBER em aberto agrupados por cliente.
    A jóia pro negócio de serviço (contador: honorários por cliente). Retorna o
    total, o quanto está atrasado e a lista por cliente ordenada por valor."""
    from collections import OrderedDict
    tits = listar_titulos(pool, conta_id, status="aberto", tipo="receber")
    por: "OrderedDict" = OrderedDict()
    total = 0
    total_atraso = 0
    for t in tits:
        v = t["valor_centavos"]
        total += v
        atr = bool(t["atrasado"])
        if atr:
            total_atraso += v
        nome = t["cliente_nome"] or (t["contraparte"] or "").strip() or "— sem cliente —"
        chave = t["cliente_id"] or ("txt:" + nome)
        d = por.setdefault(chave, {"cliente_id": t["cliente_id"], "nome": nome,
                                   "total_centavos": 0, "atrasado_centavos": 0,
                                   "n": 0, "atrasado": False})
        d["total_centavos"] += v
        d["n"] += 1
        if atr:
            d["atrasado_centavos"] += v
            d["atrasado"] = True
    clientes = sorted(por.values(), key=lambda x: x["total_centavos"], reverse=True)
    return {"total_centavos": total, "atrasado_centavos": total_atraso,
            "n_titulos": len(tits),
            "n_clientes": len([c for c in clientes if c["cliente_id"]]),
            "clientes": clientes}


def resumo_titulos(pool, conta_id: int, dias: int = 7) -> dict:
    """Cards da visão geral: a pagar/receber nos próximos N dias + atrasados."""
    hoje = date.today()
    fim = hoje + timedelta(days=dias)
    with pool.connection() as c:
        rows = c.execute(
            """select tipo,
                      sum(case when vencimento between %s and %s
                               then valor_centavos else 0 end),
                      count(*) filter (where vencimento between %s and %s),
                      sum(case when vencimento < %s then valor_centavos else 0 end),
                      count(*) filter (where vencimento < %s)
                 from titulos
                where conta_id=%s and status='aberto'
                group by tipo""",
            (hoje, fim, hoje, fim, hoje, hoje, conta_id),
        ).fetchall()
    res = {"a_pagar_centavos": 0, "n_pagar": 0,
           "a_receber_centavos": 0, "n_receber": 0,
           "atrasados_pagar_centavos": 0, "n_atrasados_pagar": 0,
           "atrasados_receber_centavos": 0, "n_atrasados_receber": 0,
           "atrasados_centavos": 0, "n_atrasados": 0, "dias": dias}
    for tipo, soma, n, atras_v, atras_n in rows:
        if tipo == "pagar":
            res["a_pagar_centavos"] = int(soma or 0)
            res["n_pagar"] = int(n or 0)
            res["atrasados_pagar_centavos"] = int(atras_v or 0)
            res["n_atrasados_pagar"] = int(atras_n or 0)
        else:
            res["a_receber_centavos"] = int(soma or 0)
            res["n_receber"] = int(n or 0)
            res["atrasados_receber_centavos"] = int(atras_v or 0)
            res["n_atrasados_receber"] = int(atras_n or 0)
        # combinado mantido p/ compat com quem já lê atrasados_centavos/n_atrasados
        res["atrasados_centavos"] += int(atras_v or 0)
        res["n_atrasados"] += int(atras_n or 0)
    return res


def prazo_do_vencimento(vencimento, hoje) -> tuple[str, str]:
    """"há 20 dias", "amanhã", "em 6 dias" — e a cor. ("", "") sem data.

    Mora aqui porque duas telas dizem a mesma coisa e não podem dizer diferente:
    o relatório de Contas a pagar (onde ela substituiu a coluna Status) e a lista
    da aba Empresa. "Vencida" e "atrasado" são `vencimento < hoje` — a data ao
    lado já continha o fato. O que faltava é a distância: dever há 4 dias e dever
    há 20 é a mesma palavra e urgências diferentes, e é a distância que decide
    quem se liga primeiro.
    """
    if not vencimento or not hoje:
        return "", ""
    dias = (vencimento - hoje).days
    if dias < -1:
        return f"há {-dias} dias", "erro"
    if dias == -1:
        return "ontem", "erro"
    if dias == 0:
        return "hoje", "aviso"
    if dias == 1:
        return "amanhã", "aviso"
    return f"em {dias} dias", ("aviso" if dias <= 7 else "ok")


def dar_baixa_titulo(pool, conta_id: int, titulo_id: int,
                     data_pagto: date | None = None,
                     membro_id: int | None = None) -> dict:
    """Marca o título como pago e LANÇA no livro-caixa (fonte única).

    pagar → despesa; receber → receita. Se recorrente, já cria o do mês
    seguinte. Idempotente: título já pago/cancelado não lança de novo.
    """
    data_pagto = data_pagto or date.today()
    # Baixa ATÔMICA numa ÚNICA transação/conexão: o status vira num UPDATE ...
    # WHERE status='aberto' RETURNING e, na MESMA conexão, o lançamento entra
    # (adicionar(conn=c)) e o lancamento_id/recorrente são gravados — tudo commita
    # junto. Benefícios sobre a versão de 3 conexões:
    #   • título e lançamento entram juntos ou não entram (se o insert do caixa
    #     falhar, o UPDATE do status faz rollback e o título volta a 'aberto');
    #   • uma conexão por chamada — dispensa pool com max_size ≥ 2.
    # A corrida (webhook duplicado / duplo-toque) segue fechada: o UPDATE toma o
    # lock da linha até o commit; a 2ª chamada reavalia o WHERE já com 'pago' e
    # volta vazia, sem lançar.
    proximo_id = None
    with pool.connection() as c:
        # `pago_sem_autorizacao` é carimbado no MESMO update da baixa, e é a única
        # consequência de a conta não estar liberada: o dono escolheu "só avisa,
        # não trava" em 03/09/2026. Sem esta marca a escolha não valeria nada — o
        # aviso na tela seria um clique a mais e ninguém saberia depois o que
        # passou por fora. No mesmo update, e não num segundo, porque uma marca de
        # auditoria que pode falhar sozinha depois do fato não é auditoria.
        t = c.execute(
            """update titulos
                  set status='pago', pago_em=%s,
                      pago_sem_autorizacao = (aprovacao <> 'autorizado')
                where id=%s and conta_id=%s and status='aberto'
             returning tipo, descricao, contraparte, valor_centavos, categoria,
                       recorrente, vencimento, criado_por, pago_sem_autorizacao,
                       aprovacao""",
            (data_pagto, titulo_id, conta_id),
        ).fetchone()
        if not t:
            # não ganhou a corrida: ou o título não existe, ou já não está aberto
            estado = c.execute(
                "select status from titulos where id=%s and conta_id=%s",
                (titulo_id, conta_id),
            ).fetchone()
            if not estado:
                return {"ok": False, "erro": "Título não encontrado."}
            return {"ok": False, "erro": f"Título já está '{estado[0]}'."}

        tipo_lanc = Tipo.DESPESA if t[0] == "pagar" else Tipo.RECEITA
        quem = f" — {t[2]}" if t[2] else ""
        lanc = Lancamento(tipo=tipo_lanc, valor_centavos=int(t[3]),
                          categoria=t[4] or (CAT_FORNECEDORES if t[0] == "pagar"
                                             else CAT_VENDAS),
                          descricao=f"{t[1]}{quem}", data=data_pagto,
                          origem="titulo", natureza="empresa")
        # A quem o lançamento pertence: a QUEM ORIGINOU o título (titulos.criado_por),
        # não a quem clicou em "pago". Antes ia o `membro_id` da baixa — então a
        # comissão da venda ia parar em quem deu baixa (quase sempre o dono, ou
        # ninguém), e o vendedor que fechou nunca aparecia no relatório.
        # Sem origem registrada, fica sem dono: chutar em quem clicou é o bug.
        vendedor_id = t[7] if t[7] is not None else None
        # mesma conexão c: o lançamento é atômico com a baixa do título
        salvo = LivroCaixa(pool, conta_id, vendedor_id).adicionar(
            lanc, forcar=True, conn=c)

        c.execute(
            """update titulos set lancamento_id=%s
                where id=%s and conta_id=%s""",
            (salvo.id, titulo_id, conta_id),
        )
        if t[5]:  # recorrente mensal: cria o próximo
            prox = _mes_seguinte(t[6])
            r = c.execute(
                # criado_por vai junto: a mensalidade do mês que vem é da MESMA
                # venda. Sem carregar isso, o vendedor recebia comissão no primeiro
                # mês e o resto da recorrência virava "Sem vendedor".
                # `aprovacao` vai junto pelo MESMO motivo do criado_por logo
                # acima: a parcela do mês que vem não é uma decisão nova. O
                # aluguel que o dono liberou em janeiro não pode voltar a
                # perguntar em fevereiro, senão a fila de conferência vira uma
                # cobrança mensal do que já foi respondido — e fila que repete
                # pergunta respondida é fila que alguém desliga.
                """insert into titulos
                     (conta_id, tipo, descricao, contraparte, valor_centavos,
                      vencimento, categoria, recorrente, criado_por, aprovacao)
                   values (%s,%s,%s,%s,%s,%s,%s,true,%s,%s) returning id""",
                (conta_id, t[0], t[1], t[2], int(t[3]), prox, t[4], t[7], t[9]),
            ).fetchone()
            proximo_id = r[0]
        c.commit()
    return {"ok": True, "lancamento_id": salvo.id, "proximo_titulo_id": proximo_id,
            "sem_autorizacao": bool(t[8])}


# Quantos dias em volta do vencimento um pagamento ainda pode ser DAQUELE título.
# Mora aqui, e não na tela, porque as duas pontas TÊM que usar o mesmo número: a
# aba sugere com ele e a gravação revalida com ele. Se divergirem, aparece botão
# que o servidor recusa — ou, pior, botão que grava o que a tela não sugeriu.
#
# O teto é dado pela conta de menor espaçamento da base, que é a QUINZENAL (15
# dias), não a mensal. Medido: com 15 a régua devolvia 3 dicas na Prime e 2 eram
# falsas, porque os títulos "2ª quinzena agosto" vencem em 05/09 e o pagamento da
# 1ª quinzena caiu em 21/08 — exatamente 15 dias antes.
JANELA_CONCILIACAO_DIAS = 14


# ─────────────────────────────────────────────────────────────────────────────
# A segunda régua: o TEXTO. Valor + data não bastam, e a Prime provou.
#
# Em 04/09/2026 a aba avisava 5 contas como "talvez já paga" e DUAS estavam
# simplesmente erradas: as duas contas da Jaqueline Duarte, R$ 3.000,00 de dívida
# real, apareciam marcadas por causa de três pagamentos de R$ 1.500,00 que são do
# Pedro Yan, do Thiago e de um reembolso ao cliente Jonas Barros. Nenhum dela. Um
# aviso desses não é só ruído: convence o dono a NÃO pagar quem ele deve.
#
# O contrapeso, e é ele que decide o desenho: **deixar de avisar é pior que
# avisar à toa.** Perder um aviso custa pagar duas vezes — dinheiro que sai. Por
# isso aqui não se recusa por "não achei parecença"; recusa-se só por
# CONTRADIÇÃO, que é evidência positiva de que são coisas diferentes. Na dúvida,
# o aviso fica de pé.
#
# São duas contradições, e cada uma tem a sua condição de confiança:
#
# 1. PERÍODO. Os dois textos dizem um período e os períodos são outros —
#    "1 quinzena setembro/26" contra "2ª quinzena ago/26". Isso não é palpite:
#    são compromissos diferentes, e vale venha o texto de onde vier. Só mês por
#    NOME conta; "IPTU 2026 3/6" não vira março nem junho.
#
# 2. NOME, e só quando dá pra confiar no texto do pagamento. Extrato de banco
#    escreve "Pagamento Pix 58.608.090 0001-88" — um CNPJ, nome nenhum; recusar
#    por aí mataria justamente a única dica boa que a produção inteira produziu
#    (a ZARB, que veio do extrato). Já a foto do comprovante e o lançamento
#    escrito à mão nomeiam o beneficiário: "Serviço prestado - Thiago Cesar
#    Borges Pinheiro". Aí, se o nome não encosta em nada do título nem do
#    fornecedor dele, é outro pagamento.
_ORIGENS_COM_NOME = {"foto", "manual", "balcao", "folha"}

_MESES = {"janeiro": 1, "jan": 1, "fevereiro": 2, "fev": 2, "marco": 3, "mar": 3,
          "abril": 4, "abr": 4, "maio": 5, "mai": 5, "junho": 6, "jun": 6,
          "julho": 7, "jul": 7, "agosto": 8, "ago": 8, "setembro": 9, "set": 9,
          "outubro": 10, "out": 10, "novembro": 11, "nov": 11,
          "dezembro": 12, "dez": 12}

# Palavras que não identificam ninguém. Lista generosa de propósito: quanto mais
# palavra genérica sai, menor a assinatura, mais fácil ela ficar VAZIA — e
# assinatura vazia nunca recusa nada. Errar pra cá deixa o aviso de pé.
_SEM_IDENTIDADE = {
    # período e recorrência
    "quinzena", "quinzenal", "mensalidade", "mensal", "mes", "meses", "semana",
    "semanal", "ano", "anual", "dia", "dias", "periodo", "primeira", "segunda",
    "ref", "referencia", "referente", "competencia", "vencimento",
    # dinheiro andando
    "pagamento", "pagto", "pago", "pix", "ted", "doc", "transferencia",
    "boleto", "deposito", "saque", "comprovante", "recibo", "nota", "fiscal",
    "fatura", "parcela", "parcelas", "valor", "total", "juros", "multa",
    "desconto", "adiantamento", "reembolso", "estorno", "acordo", "credito",
    "debito",
    # o que a coisa é
    "servico", "servicos", "prestado", "prestados", "prestacao", "produto",
    "produtos", "compra", "venda", "conta", "contas", "despesa", "receita",
    "cliente", "fornecedor", "funcionario", "colaborador", "empresa",
    # sufixo de razão social
    "ltda", "epp", "eireli", "cia", "sociedade", "comercio", "industria",
    "assessoria", "consultoria", "administrativa", "administrativo",
    "servicos", "solucoes", "grupo", "filial", "matriz",
}


def _sem_acento(texto) -> str:
    """Minúscula, sem acento, só letra e número — o resto vira espaço.

    NFKD e não NFD por causa do "2ª quinzena", que é como o agente escreve o
    comprovante. O "ª" é U+00AA, e pro Python ele é ALFANUMÉRICO: sobrevive ao
    NFD e à limpeza, e "2ª quinzena" não casa com nenhuma régua que espere
    "2a quinzena". O NFKD abre o ordinal em "a" e o problema some.
    """
    import unicodedata
    bruto = unicodedata.normalize("NFKD", str(texto or ""))
    limpo = "".join(c for c in bruto if unicodedata.category(c) != "Mn")
    return "".join(c if c.isalnum() else " " for c in limpo.lower())


def assinatura_de_nome(*textos) -> set[str]:
    """As palavras de um texto que servem pra dizer QUEM é.

    Fora: número (CNPJ, ano, parcela), palavra curta (até 3 letras — "de", "da",
    "e", e também o "26" de "ago/26"), mês, e a lista de genéricas acima. O que
    sobra é nome próprio, marca ou coisa específica.
    """
    fora = set()
    for t in textos:
        for p in _sem_acento(t).split():
            if len(p) < 4 or p.isdigit():
                continue
            if p in _SEM_IDENTIDADE or p in _MESES:
                continue
            fora.add(p)
    return fora


def _nomes_se_encostam(a: set[str], b: set[str]) -> bool:
    """Duas assinaturas se encostam se QUALQUER palavra de uma cabe na outra.

    Contenção, e não igualdade, por causa do apelido: na Prime o título é
    "2 QUINZENA AGOSTO/26 BETO" e o fornecedor é "ROBERTO LOPES" — "beto" está
    dentro de "roberto". Igualdade estrita recusaria o pagamento certo. E o erro
    aqui só pode ser pra um lado: encostar demais mantém o aviso, que é o lado
    barato.
    """
    for x in a:
        for y in b:
            if x in y or y in x:
                return True
    return False


def periodo_no_texto(texto) -> tuple[int | None, int | None] | None:
    """(quinzena, mês) achados no texto, ou None se ele não fala de período.

    Mês só por NOME — "IPTU 2026 3/6" não pode virar março. Quinzena só colada na
    palavra: "1 QUINZENA SETEMBRO", "2ª quinzena ago/26", "quinzena 1".
    """
    import re
    limpo = _sem_acento(texto)
    mes = None
    for p in limpo.split():
        if p in _MESES:
            mes = _MESES[p]
            break
    quinzena = None
    m = (re.search(r"(\d)\s*a?\s+quinzena", limpo)
         or re.search(r"quinzena\s+(\d)", limpo))
    if m and m.group(1) in ("1", "2"):
        quinzena = int(m.group(1))
    return None if (mes is None and quinzena is None) else (quinzena, mes)


def texto_contradiz(titulo: dict, lanc: dict) -> str | None:
    """O que no TEXTO diz que este pagamento não é deste título — ou None.

    Devolve motivo legível porque ele vai virar mensagem de erro na gravação. Só
    responde por contradição; ausência de parecença não é motivo.
    """
    do_titulo = f"{titulo.get('descricao') or ''} {titulo.get('contraparte') or ''}"
    do_lanc = lanc.get("descricao") or ""

    p_tit, p_lanc = periodo_no_texto(do_titulo), periodo_no_texto(do_lanc)
    if p_tit and p_lanc:
        q_t, m_t = p_tit
        q_l, m_l = p_lanc
        if (m_t is not None and m_l is not None and m_t != m_l) or \
           (q_t is not None and q_l is not None and q_t != q_l):
            return ("Esse pagamento é de outro período — a conta e o pagamento "
                    "falam de quinzenas/meses diferentes.")

    if (lanc.get("origem") or "") in _ORIGENS_COM_NOME:
        a, b = assinatura_de_nome(do_titulo), assinatura_de_nome(do_lanc)
        # assinatura vazia dos dois lados = texto que não nomeia ninguém. Não dá
        # pra contradizer com o que não foi dito.
        if a and b and not _nomes_se_encostam(a, b):
            return ("Esse pagamento está em nome de outra pessoa — o comprovante "
                    "não fala da mesma conta.")
    return None


def pagamento_serve_pro_titulo(titulo: dict, lanc: dict) -> str | None:
    """O erro que impede este pagamento de quitar este título, ou None se serve.

    Existe separada e sem banco porque é a régua que a tela usa pra sugerir e que
    a gravação usa pra revalidar. O pedido vem do navegador, e navegador não é
    fonte confiável: sem revalidar aqui, um POST forjado casaria qualquer
    lançamento com qualquer título.
    """
    if titulo["status"] != "aberto":
        return f"Essa conta já está '{titulo['status']}'."
    esperado = "despesa" if titulo["tipo"] == "pagar" else "receita"
    if lanc["tipo"] != esperado:
        return "Esse lançamento é do outro lado do caixa."
    if int(lanc["valor_centavos"]) != int(titulo["valor_centavos"]):
        return "O valor do pagamento não bate com o da conta."
    if not titulo["vencimento"] or not lanc["data"]:
        return "Falta a data pra conferir se é esse pagamento."
    if abs((lanc["data"] - titulo["vencimento"]).days) > JANELA_CONCILIACAO_DIAS:
        return (f"O pagamento está a mais de {JANELA_CONCILIACAO_DIAS} dias do "
                "vencimento — pode ser de outro mês.")
    # e o texto, pelo mesmo motivo que a janela mora aqui: a tela deixou de
    # sugerir estes casos, e a gravação tem que recusar os mesmos. Sem isto um
    # POST forjado — ou um formulário aberto antes da mudança — fecharia a conta
    # de setembro com o dinheiro de agosto.
    return texto_contradiz(titulo, lanc)


def conciliar_titulo(pool, conta_id: int, titulo_id: int, lancamento_id: int) -> dict:
    """Amarra um pagamento QUE JÁ EXISTE a um título aberto, e fecha o título.

    É a irmã de `dar_baixa_titulo`, pra um caso que ela NÃO atende. Aquela lança
    no livro-caixa: serve pra quando o dinheiro sai no momento do clique. Aqui o
    dinheiro já saiu — veio do extrato ou da foto do comprovante — e chamar
    `dar_baixa_titulo` criaria um segundo lançamento, **dobrando a despesa no
    livro-caixa e no DRE**. Por isso esta função não lança nada: só liga o que já
    existe.

    NÃO cria o título recorrente do mês seguinte, e isso é de propósito — outra
    diferença que vem do caso de uso. `dar_baixa_titulo` roda na hora do
    pagamento, então "criar o do mês que vem" faz sentido. Conciliação é
    retroativa: em 01/09/2026 a Prime tem 30 títulos de setembro já cadastrados na
    mão, e conciliar a ZARB de 15/08 criaria uma segunda ZARB de 15/09 em cima da
    que já está lá. Conta duplicada é a mesma família de erro que a conta escondida.

    Atômico e à prova de duplo-clique pelo mesmo padrão da irmã: o status vira num
    único UPDATE ... WHERE status='aberto' RETURNING, que toma o lock da linha até
    o commit. A segunda chamada reavalia o WHERE já com 'pago' e volta com erro,
    sem gravar nada.
    """
    with pool.connection() as c:
        l = c.execute(
            """select id, data, valor_centavos, tipo, origem, descricao
                 from lancamentos where id=%s and conta_id=%s""",
            (lancamento_id, conta_id),
        ).fetchone()
        if not l:
            return {"ok": False, "erro": "Pagamento não encontrado."}
        lanc = {"id": l[0], "data": l[1], "valor_centavos": int(l[2] or 0),
                "tipo": l[3], "origem": l[4], "descricao": l[5]}

        dono = c.execute(
            "select id from titulos where lancamento_id=%s and conta_id=%s",
            (lancamento_id, conta_id),
        ).fetchone()
        if dono:
            return {"ok": False, "erro": "Esse pagamento já está ligado a outra conta."}

        # o gêmeo: pagamento de mesmo dia e valor que uma baixa de título é o ECO
        # daquele dinheiro, não dinheiro novo. Sem esta trava, a foto do
        # comprovante do sinal da Bianca Oliveira quitaria a parcela 2/2 — fechando
        # com o dinheiro da parcela 1 uma dívida que a cliente ainda tem.
        if lanc["origem"] != "titulo" and c.execute(
                """select 1 from lancamentos
                    where conta_id=%s and origem='titulo' and tipo=%s and data=%s
                      and valor_centavos=%s limit 1""",
                (conta_id, lanc["tipo"], lanc["data"], lanc["valor_centavos"]),
        ).fetchone():
            return {"ok": False, "erro": "Esse dinheiro já foi contado na baixa de "
                                         "outra conta — é a mesma entrada, repetida."}

        # `contraparte` entra junto da descrição porque a régua do texto lê as
        # duas: na Prime o título diz "2 QUINZENA AGOSTO/26 BETO" e só a
        # contraparte ("ROBERTO LOPES") permite reconhecer o pagamento em nome
        # dele. Sem ela, o apelido recusaria o dinheiro certo.
        t = c.execute(
            """select tipo, valor_centavos, vencimento, status, descricao, contraparte
                 from titulos where id=%s and conta_id=%s""",
            (titulo_id, conta_id),
        ).fetchone()
        if not t:
            return {"ok": False, "erro": "Conta não encontrada."}
        titulo = {"tipo": t[0], "valor_centavos": int(t[1] or 0), "vencimento": t[2],
                  "status": t[3], "descricao": t[4], "contraparte": t[5]}
        erro = pagamento_serve_pro_titulo(titulo, lanc)
        if erro:
            return {"ok": False, "erro": erro}

        # pago_em é a data em que o DINHEIRO andou, não a de hoje: é ela que decide
        # o mês do compromisso nos relatórios
        feito = c.execute(
            """update titulos set status='pago', pago_em=%s, lancamento_id=%s
                where id=%s and conta_id=%s and status='aberto' returning id""",
            (lanc["data"], lancamento_id, titulo_id, conta_id),
        ).fetchone()
        if not feito:
            return {"ok": False, "erro": "Essa conta acabou de mudar de estado. "
                                         "Recarregue a tela."}
        c.commit()
    return {"ok": True, "titulo_id": titulo_id, "lancamento_id": lancamento_id,
            "descricao": titulo["descricao"], "pago_em": lanc["data"],
            "valor_centavos": titulo["valor_centavos"], "tipo": titulo["tipo"]}


def desfazer_conciliacao(pool, conta_id: int, titulo_id: int) -> dict:
    """Reabre um título que foi conciliado por engano.

    Existe porque a conciliação nasce de um PALPITE — "esse Pix parece ser desta
    conta" — e ação de um clique baseada em palpite, numa tela de dinheiro, sem
    volta, é como se perde informação do cliente. Marcar pago o que não foi pago
    esconde uma dívida; sem desfazer, o único jeito de corrigir seria mexer no
    banco na mão.

    Só desfaz CONCILIAÇÃO, nunca uma baixa comum, e o que separa as duas é a
    origem do lançamento amarrado: baixa comum sempre cria o lançamento dela com
    `origem='titulo'`, e reabrir esse caso deixaria o lançamento órfão no
    livro-caixa (dinheiro sem dono). Aqui o lançamento é de fora — extrato, foto,
    manual — e continua exatamente onde estava; some só o vínculo.
    """
    with pool.connection() as c:
        r = c.execute(
            """select t.status, l.origem from titulos t
                 left join lancamentos l on l.id = t.lancamento_id
                where t.id=%s and t.conta_id=%s""",
            (titulo_id, conta_id),
        ).fetchone()
        if not r:
            return {"ok": False, "erro": "Conta não encontrada."}
        if r[0] != "pago":
            return {"ok": False, "erro": f"Essa conta está '{r[0]}', não paga."}
        if r[1] is None:
            return {"ok": False, "erro": "Essa baixa não veio de uma conciliação."}
        if r[1] == "titulo":
            return {"ok": False, "erro": "Essa baixa lançou dinheiro no caixa — "
                                         "desfazer deixaria o lançamento sem dono."}
        feito = c.execute(
            """update titulos set status='aberto', pago_em=null, lancamento_id=null
                where id=%s and conta_id=%s and status='pago' returning descricao""",
            (titulo_id, conta_id),
        ).fetchone()
        if not feito:
            return {"ok": False, "erro": "Essa conta acabou de mudar de estado."}
        c.commit()
    return {"ok": True, "descricao": feito[0]}


def cancelar_titulo(pool, conta_id: int, titulo_id: int) -> bool:
    with pool.connection() as c:
        r = c.execute(
            """update titulos set status='cancelado'
                where id=%s and conta_id=%s and status='aberto' returning id""",
            (titulo_id, conta_id),
        ).fetchone()
        c.commit()
    return r is not None


def editar_descricao_titulo(pool, conta_id: int, titulo_id: int,
                            nova_descricao: str) -> bool:
    """Atalho: edita só a descrição. Mantido por compatibilidade."""
    return editar_titulo(pool, conta_id, titulo_id, descricao=nova_descricao)


def editar_titulo(pool, conta_id: int, titulo_id: int,
                  descricao: str | None = None,
                  valor_centavos: int | None = None,
                  contraparte: str | None = None,
                  cliente_id: int | None = None) -> bool:
    """Corrige descrição, valor e/ou FORNECEDOR de um título. NÃO mexe em
    vencimento nem tipo. Multi-tenant: só o título DESTA conta. Passa só o que
    quer mudar; campo None é ignorado. Descrição vazia é ignorada (não apaga);
    valor negativo é rejeitado. Retorna True se algo mudou.

    O FORNECEDOR entrou aqui em 03/09/2026, e a falta dele era o buraco: quem
    salvasse um título sem fornecedor não tinha mais onde colocar — nem digitando
    nem escolhendo. Medido na Prime no mesmo dia: **30 de 30 títulos a pagar sem
    fornecedor**, com o nome enfiado na descrição ("ZARB CONSULTORIA",
    "EQUATORIAL", "BANCO DO NORDESTE"). Não era descuido de quem lança; era a
    tela não tendo o campo.

    `contraparte` vazia AGORA APAGA, ao contrário da descrição: limpar o
    fornecedor é uma correção legítima (foi ligado no errado), e nome de
    fornecedor errado é pior que nome nenhum. `cliente_id=0` desliga a ficha pelo
    mesmo motivo — None continua sendo "não mexe"."""
    sets, args = [], []
    if descricao is not None:
        desc = descricao.strip()[:200]
        if desc:
            sets.append("descricao=%s"); args.append(desc)
    if valor_centavos is not None and int(valor_centavos) >= 0:
        sets.append("valor_centavos=%s"); args.append(int(valor_centavos))
    if contraparte is not None:
        sets.append("contraparte=%s"); args.append(contraparte.strip()[:200])
    if cliente_id is not None:
        sets.append("cliente_id=%s"); args.append(int(cliente_id) or None)
    if not sets:
        return False
    with pool.connection() as c:
        cur = c.execute(
            f"update titulos set {', '.join(sets)} where id=%s and conta_id=%s",
            (*args, titulo_id, conta_id),
        )
        c.commit()
        return cur.rowcount > 0


def decidir_aprovacao(pool, conta_id: int, ids, decisao: str,
                      membro_id: int | None = None, motivo: str = "") -> int:
    """O dono libera (ou recusa) contas a pagar. Devolve quantas mudaram.

    Aceita um id ou uma lista — o lote não é conveniência: quando o controle é
    ligado numa empresa que já tem 30 títulos, decidir de um em um é o que faz
    alguém desligar o controle na semana seguinte.

    SÓ MEXE EM TÍTULO ABERTO. Reabrir a discussão de algo já pago não muda o
    dinheiro que saiu e só embaralharia o histórico; o que ficou pago sem
    liberação tem marca própria (`pago_sem_autorizacao`).

    E SÓ NO QUE MUDA (`aprovacao <> decisao`). Desde 04/09/2026 a liberação em
    lote mora no relatório de Contas a pagar, onde o dono marca linhas olhando a
    lista inteira — e uma linha já liberada pode entrar na marcação por engano.
    Sem este filtro ela seria recarimbada: `aprovado_em` de hoje numa conta
    decidida semana passada, e o aviso dizendo "5 contas liberadas" quando só uma
    precisava. O que se conta é o que mudou.

    Não valida PAPEL de propósito — quem sabe quem está logado é a tela, e é lá
    que o portão fica. Este módulo não tem sessão."""
    if decisao not in APROVACOES:
        raise ValueError(f"decisão inválida: {decisao}")
    lista = [int(i) for i in (ids if isinstance(ids, (list, tuple, set)) else [ids])]
    if not lista:
        return 0
    with pool.connection() as c:
        cur = c.execute(
            """update titulos
                  set aprovacao=%s, aprovado_por=%s, aprovado_em=now(),
                      aprovacao_motivo=%s
                where conta_id=%s and id = any(%s) and status='aberto'
                  and aprovacao <> %s""",
            (decisao, membro_id, (motivo or "").strip()[:300] or None,
             conta_id, lista, decisao))
        c.commit()
    return cur.rowcount


def aguardando_aprovacao(pool, conta_id: int, tipo: str = "pagar") -> list[dict]:
    """A fila do dono: o que foi lançado e espera liberação. Só `aberto`."""
    with pool.connection() as c:
        rows = c.execute(
            """select t.id, t.descricao, t.contraparte, t.valor_centavos,
                      t.vencimento, t.criado_em,
                      coalesce(nullif(m.nome,''), m.email, '') as quem
                 from titulos t
                 left join membros m on m.id = t.criado_por
                where t.conta_id=%s and t.tipo=%s and t.status='aberto'
                  and t.aprovacao='aguardando'
                order by t.vencimento asc, t.id asc""",
            (conta_id, tipo)).fetchall()
    return [{"id": r[0], "descricao": r[1], "contraparte": r[2],
             "valor_centavos": int(r[3] or 0), "vencimento": r[4],
             "criado_em": r[5], "quem": r[6]} for r in rows]


def apagar_titulo(pool, conta_id: int, titulo_id: int) -> bool:
    """Apaga de vez um titulo lancado por engano. Retorna True se apagou.

    A TRAVA e' `lancamento_id is null`, e nao o status. A regra que importa nao e'
    "so' aberto" — e' *nada no livro-caixa depende deste titulo*. Um titulo pago pelo
    `dar_baixa_titulo` carrega o id do lancamento que a baixa criou; apagar so' o
    titulo deixaria aquele dinheiro no caixa sem origem, e o DRE e o relatorio do
    contador passariam a mostrar receita que nao se explica. Esse caso continua
    recusado — pra ele o caminho e' apagar o LANCAMENTO no financeiro, que e' onde o
    dinheiro esta'.

    O que isso libera, e por que: titulo `pago` com `lancamento_id` NULO. Ele existe
    de verdade — a FK e' `on delete set null`, entao quem apaga o lancamento do caixa
    deixa o titulo pago apontando pra nada. Sem esta regra o registro ficava preso pra
    sempre: nao aparecia na lista de abertos (a tela so' mostra 'aberto') e nenhum
    caminho o removia. Visto em producao numa conta de teste.
    """
    with pool.connection() as c:
        cur = c.execute(
            "delete from titulos where id=%s and conta_id=%s and lancamento_id is null",
            (titulo_id, conta_id),
        )
        c.commit()
        return cur.rowcount > 0


def registrar_link_cobranca(pool, conta_id: int, titulo_id: int, url: str) -> None:
    """Guarda o link Asaas do 'cobrar via Pix' (títulos a receber)."""
    with pool.connection() as c:
        c.execute(
            "update titulos set cobranca_link_url=%s where id=%s and conta_id=%s",
            (url, titulo_id, conta_id),
        )
        c.commit()


def marcar_titulo_recebido(pool, conta_id: int, titulo_id: int) -> dict:
    """Webhook Asaas: cobrança do título paga online → baixa automática."""
    return dar_baixa_titulo(pool, conta_id, titulo_id)


def _mes_seguinte(d: date) -> date:
    ano, mes = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    dia = min(d.day, 28)  # evita 31/fev
    return date(ano, mes, dia)


# ─────────────────────────────────────────────────────────────────────────
# Equipe e folha gerencial
# ─────────────────────────────────────────────────────────────────────────
def custo_real_centavos(salario_centavos: int, pro_labore: bool = False) -> int:
    """Custo mensal estimado: salário + FGTS 8% + provisões de 13º e férias+1/3.

    Estimativa GERENCIAL (Simples Nacional; INSS patronal está no DAS).
    Pró-labore não carrega esses encargos aqui (fator 1).
    """
    if pro_labore:
        return int(salario_centavos)
    return int(round(salario_centavos * FATOR_ENCARGOS))


def criar_funcionario(pool, conta_id: int, nome: str, cargo: str = "",
                      salario_centavos: int = 0, dia_pagamento: int = 5,
                      pro_labore: bool = False, vale_transporte: bool = False,
                      admitido_em: date | None = None, cbo: str = "",
                      departamento: str = "", setor: str = "",
                      secao: str = "", cpf: str = "") -> dict:
    with pool.connection() as c:
        r = c.execute(
            """insert into funcionarios
                 (conta_id, nome, cargo, salario_centavos, dia_pagamento,
                  pro_labore, vale_transporte, admitido_em, cbo,
                  departamento, setor, secao, cpf)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
            (conta_id, (nome or "").strip(), (cargo or "").strip(),
             int(salario_centavos), int(dia_pagamento), bool(pro_labore),
             bool(vale_transporte), admitido_em, (cbo or "").strip() or None,
             (departamento or "").strip() or None, (setor or "").strip() or None,
             (secao or "").strip() or None,
             "".join(ch for ch in (cpf or "") if ch.isdigit()) or None),
        ).fetchone()
        # A vigência INICIAL nasce junto (migração 150). Sem ela, o primeiro
        # aumento criaria a única linha da linha do tempo — e aí a folha de um mês
        # ANTERIOR ao aumento não acharia vigência nenhuma, cairia na reserva
        # (funcionarios.salario_centavos, já atualizado pro valor novo) e
        # reimprimiria o holerite antigo com o salário de hoje. Que é exatamente o
        # erro silencioso que a vigência existe pra impedir.
        c.execute(
            """insert into funcionario_salarios
                 (conta_id, funcionario_id, salario_centavos, vigencia_de)
               values (%s,%s,%s,%s)
               on conflict (funcionario_id, vigencia_de) do nothing""",
            (conta_id, r[0], int(salario_centavos),
             admitido_em or date(1900, 1, 1)),
        )
        c.commit()
    return {"id": r[0], "nome": nome, "salario_centavos": int(salario_centavos)}


def atualizar_funcionario(pool, conta_id: int, funcionario_id: int, *,
                          nome: str | None = None, cargo: str | None = None,
                          salario_centavos: int | None = None,
                          dia_pagamento: int | None = None,
                          vale_transporte: bool | None = None,
                          cbo: str | None = None,
                          departamento: str | None = None,
                          setor: str | None = None,
                          secao: str | None = None,
                          admitido_em: "date | bool | None" = False,
                          demitido_em: "date | bool | None" = False,
                          cpf: str | None = None) -> bool:
    # admitido_em/demitido_em usam sentinela False = "não mexer" (None é um
    # valor válido: limpar a data).
    sets, args = [], []
    if nome is not None:
        sets.append("nome=%s"); args.append(nome.strip())
    if cargo is not None:
        sets.append("cargo=%s"); args.append(cargo.strip())
    if salario_centavos is not None:
        sets.append("salario_centavos=%s"); args.append(int(salario_centavos))
    if dia_pagamento is not None:
        sets.append("dia_pagamento=%s"); args.append(int(dia_pagamento))
    if vale_transporte is not None:
        sets.append("vale_transporte=%s"); args.append(bool(vale_transporte))
    if cbo is not None:
        sets.append("cbo=%s"); args.append((cbo or "").strip() or None)
    if departamento is not None:
        sets.append("departamento=%s"); args.append((departamento or "").strip() or None)
    if setor is not None:
        sets.append("setor=%s"); args.append((setor or "").strip() or None)
    if secao is not None:
        sets.append("secao=%s"); args.append((secao or "").strip() or None)
    if admitido_em is not False:
        sets.append("admitido_em=%s"); args.append(admitido_em)
    if demitido_em is not False:
        sets.append("demitido_em=%s"); args.append(demitido_em)
    if cpf is not None:
        sets.append("cpf=%s"); args.append("".join(ch for ch in cpf if ch.isdigit()) or None)
    if not sets:
        return False
    with pool.connection() as c:
        r = c.execute(
            f"update funcionarios set {', '.join(sets)} "
            "where id=%s and conta_id=%s returning id",
            (*args, funcionario_id, conta_id),
        ).fetchone()
        c.commit()
    return r is not None


# ------------------------------------------------------------------ salário
# O salário tem VIGÊNCIA (migração 150): cada valor vale a partir de uma data, e
# a folha de cada mês usa o que valia naquela competência. Sem isso, dar um
# aumento sobrescrevia o número e o holerite de um mês passado, reimpresso, saía
# com o valor novo — um valor que a pessoa não recebeu, num documento que ela
# guarda.
#
# `funcionarios.salario_centavos` continua sendo o salário CORRENTE (é o que o
# formulário mostra e o que criar_funcionario grava). Esta tabela é a linha do
# tempo.

def definir_salario(pool, conta_id: int, funcionario_id: int,
                    salario_centavos: int, vigencia_de: date) -> bool:
    """Aumento (ou redução): o novo valor passa a valer A PARTIR de `vigencia_de`.
    Os meses anteriores continuam com o valor que tinham — é isso que mantém o
    holerite antigo correto.

    Regravar a MESMA data substitui o valor daquela vigência (dá pra corrigir a
    data que se acabou de lançar sem sujar o histórico com duas linhas)."""
    with pool.connection() as c:
        r = c.execute(
            """insert into funcionario_salarios
                 (conta_id, funcionario_id, salario_centavos, vigencia_de)
               values (%s,%s,%s,%s)
               on conflict (funcionario_id, vigencia_de)
               do update set salario_centavos=excluded.salario_centavos
               returning id""",
            (conta_id, funcionario_id, int(salario_centavos), vigencia_de),
        ).fetchone()
        if r is None:
            c.commit()
            return False
        # espelha no campo corrente quando a vigência já começou: é ele que a tela
        # mostra e que serve de reserva pra quem não tem linha nenhuma.
        c.execute(
            """update funcionarios set salario_centavos=%s
                where id=%s and conta_id=%s and %s <= current_date""",
            (int(salario_centavos), funcionario_id, conta_id, vigencia_de),
        )
        c.commit()
    return True


def corrigir_salario_atual(pool, conta_id: int, funcionario_id: int,
                           salario_centavos: int) -> bool:
    """Digitou errado: reescreve a vigência MAIS RECENTE no lugar, em vez de criar
    uma nova. A diferença importa — corrigir um erro de digitação não é um
    aumento, e virar linha nova no histórico contaria uma história que não houve.

    Sem nenhuma vigência gravada (funcionário anterior à migração cujo backfill
    não pegou), cria a primeira."""
    with pool.connection() as c:
        alvo = c.execute(
            """select id from funcionario_salarios
                where funcionario_id=%s and conta_id=%s
                order by vigencia_de desc limit 1""",
            (funcionario_id, conta_id),
        ).fetchone()
        if alvo is None:
            existe = c.execute(
                "select admitido_em from funcionarios where id=%s and conta_id=%s",
                (funcionario_id, conta_id)).fetchone()
            if existe is None:
                return False
            c.execute(
                """insert into funcionario_salarios
                     (conta_id, funcionario_id, salario_centavos, vigencia_de)
                   values (%s,%s,%s,%s)
                   on conflict (funcionario_id, vigencia_de)
                   do update set salario_centavos=excluded.salario_centavos""",
                (conta_id, funcionario_id, int(salario_centavos),
                 existe[0] or date(1900, 1, 1)),
            )
        else:
            c.execute("update funcionario_salarios set salario_centavos=%s where id=%s",
                      (int(salario_centavos), alvo[0]))
        c.execute("update funcionarios set salario_centavos=%s where id=%s and conta_id=%s",
                  (int(salario_centavos), funcionario_id, conta_id))
        c.commit()
    return True


def historico_salarios(pool, conta_id: int, funcionario_id: int) -> list[dict]:
    """A linha do tempo, do mais novo pro mais antigo (é a ordem que a tela usa)."""
    with pool.connection() as c:
        rows = c.execute(
            """select salario_centavos, vigencia_de from funcionario_salarios
                where funcionario_id=%s and conta_id=%s
                order by vigencia_de desc""",
            (funcionario_id, conta_id),
        ).fetchall()
    return [{"salario_centavos": int(r[0] or 0), "vigencia_de": r[1]} for r in rows]


def historicos_salarios(pool, conta_id: int) -> dict[int, list[dict]]:
    """O histórico de TODOS os funcionários da conta, numa consulta só — o card da
    folha mostra a linha do tempo de cada um, e uma consulta por funcionário dentro
    do laço do template seria N+1 numa tela que já é pesada."""
    with pool.connection() as c:
        rows = c.execute(
            """select funcionario_id, salario_centavos, vigencia_de
                 from funcionario_salarios where conta_id=%s
                order by funcionario_id, vigencia_de desc""",
            (conta_id,),
        ).fetchall()
    saida: dict[int, list[dict]] = {}
    for fid, cent, vig in rows:
        saida.setdefault(int(fid), []).append(
            {"salario_centavos": int(cent or 0), "vigencia_de": vig})
    return saida


def _salarios_por_competencia(c, conta_id: int, competencia: date) -> dict[int, int]:
    """funcionario_id -> salário que valia na competência. Uma consulta só pra
    conta inteira (distinct on), em vez de uma por funcionário dentro do laço da
    folha."""
    rows = c.execute(
        """select distinct on (funcionario_id) funcionario_id, salario_centavos
             from funcionario_salarios
            where conta_id=%s and vigencia_de <= %s
            order by funcionario_id, vigencia_de desc""",
        (conta_id, competencia),
    ).fetchall()
    return {int(r[0]): int(r[1] or 0) for r in rows}


# Aqui existia `desativar_funcionario()` (marcava ativo=false). Foi removida: era
# código morto desde que nasceu — nenhuma tela, rota ou teste a chamava —, e
# manter uma função com esse nome ao lado do excluir de verdade é convite a usar a
# errada. Quem sai da empresa é caso de DAR BAIXA (demitido_em), que preserva o
# histórico; quem nunca deveria ter existido é caso de excluir_funcionario().
# A coluna `ativo` continua na tabela e continua sendo respeitada por
# listar_funcionarios(so_ativos=True) — só ninguém mais a escreve.


def pode_excluir_funcionario(pool, conta_id: int, funcionario_id: int) -> dict:
    """Excluir de verdade só vale pra quem NUNCA movimentou a folha — o cadastro
    duplicado ou digitado errado. Quem já teve lançamento deixaria os
    `folha_eventos` órfãos, e o holerite e o relatório daquele período ficariam
    sem dono. Esse é caso de DAR BAIXA (demitido_em), que preserva tudo.

    Devolve as contagens junto porque a tela mostra o motivo ("17 lançamentos,
    5 meses pagos") em vez de um "não pode" seco."""
    with pool.connection() as c:
        existe = c.execute("select 1 from funcionarios where id=%s and conta_id=%s",
                           (funcionario_id, conta_id)).fetchone()
        if existe is None:
            return {"pode": False, "existe": False, "lancamentos": 0, "meses_pagos": 0}
        r = c.execute(
            """select count(*)::int,
                      count(distinct competencia) filter (where tipo='pagamento')::int
                 from folha_eventos
                where conta_id=%s and funcionario_id=%s""",
            (conta_id, funcionario_id),
        ).fetchone()
    lanc, pagos = int(r[0] or 0), int(r[1] or 0)
    return {"pode": lanc == 0, "existe": True,
            "lancamentos": lanc, "meses_pagos": pagos}


def excluir_funcionario(pool, conta_id: int, funcionario_id: int) -> dict:
    """Apaga o cadastro DE VERDADE. A trava é conferida aqui dentro, não só na
    rota: assim o backend continua seguro se alguém chamar isto de outro lugar
    depois. Devolve o mesmo dicionário do pode_excluir_funcionario, com o
    resultado — quem chama sabe por que não deu."""
    situacao = pode_excluir_funcionario(pool, conta_id, funcionario_id)
    if not situacao["pode"]:
        return {**situacao, "excluido": False}
    with pool.connection() as c:
        # as vigências saem junto pelo `on delete cascade` da migração 150
        c.execute("delete from funcionarios where id=%s and conta_id=%s",
                  (funcionario_id, conta_id))
        c.commit()
    return {**situacao, "excluido": True}


def listar_funcionarios(pool, conta_id: int, so_ativos: bool = True,
                        competencia: date | None = None) -> list[dict]:
    """Com `competencia`, o salário de cada um é o que VALIA naquele mês (tabela
    funcionario_salarios). Sem ela, é o salário corrente do cadastro.

    Quem não tiver nenhuma vigência gravada cai no campo antigo — cinto de
    segurança caso o backfill da migração 150 não tenha pegado alguém; sem isso o
    funcionário apareceria com salário zero na folha, que é bem pior do que
    aparecer com o valor corrente."""
    cond = "conta_id=%s" + (" and ativo" if so_ativos else "")
    with pool.connection() as c:
        rows = c.execute(
            f"""select id, nome, cargo, salario_centavos, dia_pagamento,
                       pro_labore, ativo, admitido_em, vale_transporte, cbo,
                       departamento, setor, secao, demitido_em, cpf
                  from funcionarios where {cond}
                 order by pro_labore, nome""",
            (conta_id,),
        ).fetchall()
        vigentes = (_salarios_por_competencia(c, conta_id, competencia)
                    if competencia else {})
    saida = []
    for r in rows:
        salario = vigentes.get(int(r[0]), int(r[3] or 0)) if competencia else int(r[3] or 0)
        saida.append({
            "id": r[0], "nome": r[1], "cargo": r[2],
            "salario_centavos": salario, "dia_pagamento": int(r[4] or 5),
            "pro_labore": bool(r[5]), "ativo": bool(r[6]), "admitido_em": r[7],
            "vale_transporte": bool(r[8]), "cbo": r[9] or "",
            "departamento": r[10] or "", "setor": r[11] or "", "secao": r[12] or "",
            "demitido_em": r[13], "cpf": r[14] or "",
            "custo_real_centavos": custo_real_centavos(salario, bool(r[5])),
        })
    return saida


def registrar_evento_folha(pool, conta_id: int, funcionario_id: int, tipo: str,
                           valor_centavos: int, competencia: date | None = None,
                           descricao: str = "",
                           membro_id: int | None = None) -> dict:
    """Adiantamento / benefício / extra / desconto na competência (mês).

    ADIANTAMENTO ('vale') e BENEFÍCIO ('beneficio', ex: vale-refeição/alimentação)
    saem do caixa na hora (despesa Pessoal) — dinheiro que já foi. A diferença:
    o ADIANTAMENTO é descontado do líquido do funcionário no fechamento (é salário
    dele pago antes); o BENEFÍCIO é custo do empregador e NÃO desconta do
    funcionário. Extra e desconto só ajustam a folha; viram caixa no 'pagar folha'.
    """
    if tipo not in ("vale", "beneficio", "extra", "desconto"):
        raise ValueError("tipo deve ser 'vale', 'beneficio', 'extra' ou 'desconto'")
    comp = _competencia(competencia)
    with pool.connection() as c:
        f = c.execute(
            "select nome from funcionarios where id=%s and conta_id=%s and ativo",
            (funcionario_id, conta_id),
        ).fetchone()
    if not f:
        return {"ok": False, "erro": "Funcionário não encontrado."}

    lanc_id = None
    if tipo in ("vale", "beneficio"):
        rotulo = "Adiantamento salarial" if tipo == "vale" else "Benefício (VR/VA)"
        lanc = Lancamento(tipo=Tipo.DESPESA, valor_centavos=int(valor_centavos),
                          categoria=CAT_PESSOAL,
                          descricao=f"{rotulo} — {f[0]}"
                                    + (f" ({descricao})" if descricao else ""),
                          origem="folha", natureza="empresa")
        salvo = LivroCaixa(pool, conta_id, membro_id).adicionar(lanc, forcar=True)
        lanc_id = salvo.id

    with pool.connection() as c:
        r = c.execute(
            """insert into folha_eventos
                 (conta_id, funcionario_id, tipo, valor_centavos, competencia,
                  descricao, lancamento_id)
               values (%s,%s,%s,%s,%s,%s,%s) returning id""",
            (conta_id, funcionario_id, tipo, int(valor_centavos), comp,
             descricao, lanc_id),
        ).fetchone()
        c.commit()
    return {"ok": True, "id": r[0], "lancamento_id": lanc_id}


def folha_do_mes(pool, conta_id: int, ano: int, mes: int) -> dict:
    """A folha da competência: por funcionário, salário + extras − vales −
    descontos = a pagar; o que já foi pago; e o custo real total estimado."""
    comp = date(ano, mes, 1)
    # Demitido sai da folha a partir do mês SEGUINTE ao da demissão: aparece no
    # mês da demissão (pra acertar), some depois (fica só no histórico/relatório).
    # competencia=comp: o salário de cada um é o que VALIA neste mês, não o
    # corrente. É o que faz o holerite de um mês passado, reimpresso hoje,
    # continuar mostrando o valor que a pessoa realmente recebeu — o
    # holerite_funcionario é montado a partir daqui.
    funcs = [f for f in listar_funcionarios(pool, conta_id, so_ativos=True,
                                            competencia=comp)
             if f.get("demitido_em") is None or f["demitido_em"] >= comp]
    with pool.connection() as c:
        rows = c.execute(
            """select funcionario_id, tipo, coalesce(sum(valor_centavos),0)
                 from folha_eventos
                where conta_id=%s and competencia=%s
                group by funcionario_id, tipo""",
            (conta_id, comp),
        ).fetchall()
    ev: dict[int, dict[str, int]] = {}
    for fid, tipo, soma in rows:
        ev.setdefault(fid, {})[tipo] = int(soma or 0)

    itens, total_a_pagar, total_pago, total_custo, total_fgts = [], 0, 0, 0, 0
    for f in funcs:
        e = ev.get(f["id"], {})
        vales = e.get("vale", 0)          # adiantamento salarial (desconta)
        beneficios = e.get("beneficio", 0)  # VR/VA — custo do empregador (NÃO desconta)
        extras = e.get("extra", 0)
        descontos = e.get("desconto", 0)
        pago = e.get("pagamento", 0)
        # Descontos legais do líquido do funcionário:
        # - INSS progressivo sobre a REMUNERAÇÃO (salário + extras/horas-extra;
        #   vale/desconto não entram na base). Só CLT — pró-labore do sócio não
        #   usa esta tabela.
        # - Vale-transporte: 6% do SALÁRIO base (por lei incide sobre o salário,
        #   não sobre extras), pra quem optou.
        base_inss = f["salario_centavos"] + extras
        inss = 0 if f["pro_labore"] else inss_desconto_centavos(base_inss)
        vt = vale_transporte_desconto_centavos(
            f["salario_centavos"], f.get("vale_transporte", False))
        # FGTS 8% da remuneração: é o que o EMPREGADOR deposita no mês (não
        # desconta do funcionário). Pró-labore de sócio não gera FGTS.
        fgts = 0 if f["pro_labore"] else fgts_mes_centavos(base_inss)
        # BENEFÍCIO (VR/VA) NÃO entra no líquido — é custo do empregador, não
        # desconto do funcionário. Só o adiantamento (vales) desconta.
        liquido = f["salario_centavos"] + extras - vales - descontos - inss - vt
        a_pagar = max(liquido - pago, 0)
        total_a_pagar += a_pagar
        total_pago += pago
        total_fgts += fgts
        total_custo += custo_real_centavos(
            f["salario_centavos"] + extras, f["pro_labore"]) + beneficios
        itens.append({**f, "vales_centavos": vales, "extras_centavos": extras,
                      "beneficios_centavos": beneficios,
                      "descontos_centavos": descontos, "pago_centavos": pago,
                      "inss_centavos": inss, "vt_centavos": vt,
                      "fgts_centavos": fgts,
                      "liquido_centavos": liquido,
                      "a_pagar_centavos": a_pagar,
                      "quitado": a_pagar == 0 and liquido > 0})
    return {"competencia": comp, "itens": itens,
            "total_a_pagar_centavos": total_a_pagar,
            "total_pago_centavos": total_pago,
            "total_fgts_centavos": total_fgts,
            "custo_real_total_centavos": total_custo}


# rótulos amigáveis dos lançamentos da folha (pro histórico "corrigir")
_EVENTO_ROTULO = {"vale": "Adiantamento", "beneficio": "Benefício VR/VA",
                  "extra": "Adicional", "desconto": "Desconto"}


def eventos_folha_do_mes(pool, conta_id: int, ano: int,
                         mes: int) -> dict[int, list[dict]]:
    """Lançamentos manuais da folha na competência, por funcionário — pra listar
    e permitir CORRIGIR (remover o errado e relançar). Não inclui 'pagamento'.
    Retorna {funcionario_id: [{id, tipo, rotulo, valor_centavos, descricao, data}]}."""
    comp = date(ano, mes, 1)
    with pool.connection() as c:
        rows = c.execute(
            """select funcionario_id, id, tipo, valor_centavos, descricao, criado_em
                 from folha_eventos
                where conta_id=%s and competencia=%s
                  and tipo in ('vale','beneficio','extra','desconto')
                order by criado_em""",
            (conta_id, comp),
        ).fetchall()
    out: dict[int, list[dict]] = {}
    for fid, eid, tipo, valor, desc, criado in rows:
        out.setdefault(fid, []).append({
            "id": eid, "tipo": tipo, "rotulo": _EVENTO_ROTULO.get(tipo, tipo),
            "valor_centavos": int(valor or 0), "descricao": desc or "",
            "data": criado.date() if criado else None})
    return out


def remover_evento_folha(pool, conta_id: int, evento_id: int,
                         membro_id: int | None = None) -> bool:
    """Remove um lançamento da folha (adiantamento/benefício/extra/desconto) —
    a correção quando o valor foi digitado errado. Se o evento tinha gerado uma
    despesa no caixa (adiantamento/benefício), ela é REVERTIDA junto. Não mexe em
    'pagamento'. Multi-tenant: só remove da própria conta."""
    with pool.connection() as c:
        row = c.execute(
            """select lancamento_id, tipo from folha_eventos
                where id=%s and conta_id=%s
                  and tipo in ('vale','beneficio','extra','desconto')""",
            (evento_id, conta_id),
        ).fetchone()
        if not row:
            return False
    lanc_id = row[0]
    if lanc_id is not None:      # reverte a despesa que tinha entrado no caixa
        LivroCaixa(pool, conta_id, membro_id).apagar_lancamento(lanc_id)
    with pool.connection() as c:
        r = c.execute(
            "delete from folha_eventos where id=%s and conta_id=%s returning id",
            (evento_id, conta_id),
        ).fetchone()
        c.commit()
    return r is not None


_MESES_PT = ("", "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
             "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro")


def holerite_funcionario(pool, conta_id: int, funcionario_id: int,
                         ano: int, mes: int) -> dict | None:
    """Monta o RECIBO DE PAGAMENTO DE SALÁRIO (holerite) de um funcionário na
    competência (mês/ano): cabeçalho da empresa, dados do funcionário, verbas
    (vencimentos × descontos), totais e o rodapé (bases de INSS/FGTS/IRRF).

    Espelha EXATAMENTE a folha gerencial (folha_do_mes) — o líquido impresso é o
    mesmo que o painel mostra e que é pago. INSS/VT/FGTS conforme a lei; IRRF é
    informativo (faixa), sem retenção automática (segue com o contador).

    Retorna None se o funcionário não for da conta (ou não estiver na folha).
    """
    folha = folha_do_mes(pool, conta_id, ano, mes)
    item = next((i for i in folha["itens"] if i["id"] == funcionario_id), None)
    if item is None:
        return None

    empresa = obter_dados_empresa(pool, conta_id)
    salario = item["salario_centavos"]
    extras = item["extras_centavos"]
    inss = item["inss_centavos"]
    vt = item["vt_centavos"]
    vales = item["vales_centavos"]
    beneficios = item.get("beneficios_centavos", 0)   # VR/VA — NÃO desconta
    outros_desc = item["descontos_centavos"]
    pro_labore = item["pro_labore"]

    base_inss = salario + extras               # remuneração (base de INSS/FGTS)

    # Verbas no padrão do escritório: (código, descrição, referência, valor).
    # Proventos × Descontos, com os códigos usuais da folha.
    proventos = []
    if pro_labore:
        proventos.append(("962", "Pró-labore", "30 dia(s)", salario))
    else:
        proventos.append(("011", "Salário-Base", "30 dia(s)", salario))
    if extras:
        proventos.append(("012", "Adicionais/Vantagens", "", extras))

    descontos = []
    if inss:
        rot_inss = "11%" if pro_labore else inss_faixa_pct(base_inss)
        descontos.append(("310", "INSS", rot_inss, inss))
    if vt:
        descontos.append(("320", "Vale-Transporte", "6%", vt))
    if vales:
        descontos.append(("961", "Adiantamento Salarial", "", vales))
    if outros_desc:
        descontos.append(("990", "Outros Descontos", "", outros_desc))

    total_prov = salario + extras
    total_desc = vt + inss + vales + outros_desc
    liquido = total_prov - total_desc          # == item["liquido_centavos"]

    base_fgts = base_inss
    fgts = 0 if pro_labore else fgts_mes_centavos(base_fgts)
    base_irrf = max(0, base_inss - inss)
    irrf = irrf_info(base_irrf)

    cpf = item.get("cpf", "") or ""
    cpf_fmt = (f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}" if len(cpf) == 11 else cpf)

    return {
        "empresa": empresa,
        "competencia_mes": mes, "competencia_ano": ano,
        "competencia_label": f"{_MESES_PT[mes]}/{ano}",
        "competencia_extenso": f"{_MESES_PT[mes]} de {ano}",
        "func": {
            "id": funcionario_id,
            "codigo": f"{funcionario_id:06d}",
            "nome": item["nome"], "cargo": item["cargo"],
            "cbo": item.get("cbo", ""), "cpf": cpf_fmt,
            "lotacao": item.get("departamento", "") or "GERAL",
            "setor": item.get("setor", ""), "secao": item.get("secao", ""),
            "admitido_em": item.get("admitido_em"),
            "pro_labore": pro_labore,
        },
        "func_salario_centavos": salario,
        "beneficios_centavos": beneficios,
        "proventos": proventos, "descontos": descontos,
        "total_proventos_centavos": total_prov,
        "total_descontos_centavos": total_desc,
        "liquido_centavos": liquido,
        "salario_contratual_centavos": salario,
        "base_inss_centavos": base_inss,
        "base_fgts_centavos": base_fgts,
        "fgts_centavos": fgts,
        "base_irrf_centavos": base_irrf,
        "irrf_aliquota": irrf["aliquota"],
        "irrf_isento": irrf["isento"],
    }


def pagar_folha(pool, conta_id: int, ano: int, mes: int,
                funcionario_id: int | None = None,
                membro_id: int | None = None) -> dict:
    """Paga o restante da folha (de um funcionário, ou de todos) em 1 clique:
    registra o evento 'pagamento' e lança a despesa 'Pessoal' no caixa.

    ATÔMICO: ledger (lançamentos) + folha_eventos de todos os itens entram numa
    ÚNICA transação — ou paga a folha inteira, ou nada (nunca fica meio paga)."""
    folha = folha_do_mes(pool, conta_id, ano, mes)
    comp = folha["competencia"]
    livro = LivroCaixa(pool, conta_id, membro_id)
    pagos, total = [], 0
    with pool.connection() as c:
        for item in folha["itens"]:
            if funcionario_id is not None and item["id"] != funcionario_id:
                continue
            valor = item["a_pagar_centavos"]
            if valor <= 0:
                continue
            rotulo = "Pró-labore" if item["pro_labore"] else "Folha"
            lanc = Lancamento(tipo=Tipo.DESPESA, valor_centavos=valor,
                              categoria=CAT_PESSOAL,
                              descricao=f"{rotulo} {mes:02d}/{ano} — {item['nome']}",
                              origem="folha", natureza="empresa")
            # mesma conexão/transação do loop: ledger + folha_eventos juntos
            salvo = livro.adicionar(lanc, forcar=True, conn=c)
            c.execute(
                """insert into folha_eventos
                     (conta_id, funcionario_id, tipo, valor_centavos,
                      competencia, descricao, lancamento_id)
                   values (%s,%s,'pagamento',%s,%s,%s,%s)""",
                (conta_id, item["id"], valor, comp,
                 f"{rotulo} {mes:02d}/{ano}", salvo.id),
            )
            pagos.append({"funcionario_id": item["id"], "nome": item["nome"],
                          "valor_centavos": valor, "lancamento_id": salvo.id})
            total += valor
        c.commit()   # um único commit: ou paga a folha toda, ou nada
    return {"ok": True, "pagos": pagos, "total_centavos": total}


def _competencia(d: date | None) -> date:
    d = d or date.today()
    return date(d.year, d.month, 1)


# ─────────────────────────────────────────────────────────────────────────
# Visão do dono: fluxo projetado, DRE e relatório do contador
# ─────────────────────────────────────────────────────────────────────────
def fluxo_projetado(pool, conta_id: int, semanas: int = 4) -> dict:
    """Saldo atual (livro-caixa) + títulos abertos → saldo projetado semanal."""
    saldo = LivroCaixa(pool, conta_id).saldo_centavos()
    hoje = date.today()
    fim = hoje + timedelta(days=7 * semanas)
    with pool.connection() as c:
        rows = c.execute(
            """select vencimento, tipo, valor_centavos
                 from titulos
                where conta_id=%s and status='aberto' and vencimento <= %s
                order by vencimento""",
            (conta_id, fim),
        ).fetchall()
    pontos = []
    acumulado = saldo
    for i in range(1, semanas + 1):
        ini = hoje + timedelta(days=7 * (i - 1))
        fim_sem = hoje + timedelta(days=7 * i)
        receber = sum(int(v) for d, t, v in rows
                      if t == "receber" and d is not None and ini <= d < fim_sem)
        # atrasados (vencimento < hoje) entram todos na 1ª semana
        pagar = sum(int(v) for d, t, v in rows
                    if t == "pagar" and d is not None and ini <= d < fim_sem)
        if i == 1:
            receber += sum(int(v) for d, t, v in rows
                           if t == "receber" and d is not None and d < hoje)
            pagar += sum(int(v) for d, t, v in rows
                         if t == "pagar" and d is not None and d < hoje)
        acumulado += receber - pagar
        pontos.append({"semana": i, "ate": fim_sem,
                       "receber_centavos": receber, "pagar_centavos": pagar,
                       "saldo_centavos": acumulado})
    return {"saldo_atual_centavos": saldo, "pontos": pontos,
            "saldo_projetado_centavos": acumulado}


def dre_mes(pool, conta_id: int, ano: int, mes: int, top: int = 5) -> dict:
    """DRE simplificado do mês a partir do livro-caixa (fonte única).

    Agrega por categoria DIRETO (sem a canonização de categorias PF do
    livro-caixa, que jogaria 'Pessoal'/'Fornecedores' em 'Outros').

    Só entra o que está classificado como empresa (natureza='empresa'); os
    lançamentos "a definir" (natureza null) ficam FORA do resultado — podem ser
    gasto pessoal. Pra o número nunca sair silenciosamente magro, devolvemos
    a_definir_n/a_definir_centavos: quanto ficou de fora, pra tela avisar."""
    ini = date(ano, mes, 1)
    fim = _mes_seguinte(ini)
    with pool.connection() as c:
        rows = c.execute(
            """select tipo, categoria, sum(valor_centavos)
                 from lancamentos
                where conta_id=%s and data >= %s and data < %s
                  and natureza='empresa'
                group by tipo, categoria""",
            (conta_id, ini, fim),
        ).fetchall()
        # o que ficou de fora do DRE por ainda não estar classificado
        adef = c.execute(
            """select count(*), coalesce(sum(valor_centavos),0)
                 from lancamentos
                where conta_id=%s and data >= %s and data < %s
                  and natureza is null""",
            (conta_id, ini, fim),
        ).fetchone()
    receitas = sorted(((cat, int(v or 0)) for t, cat, v in rows
                       if t == "receita"), key=lambda kv: kv[1], reverse=True)
    despesas = sorted(((cat, int(v or 0)) for t, cat, v in rows
                       if t == "despesa"), key=lambda kv: kv[1], reverse=True)
    tot_r = sum(v for _, v in receitas)
    tot_d = sum(v for _, v in despesas)
    resultado = tot_r - tot_d
    margem = round(100.0 * resultado / tot_r, 1) if tot_r else 0.0
    # NOVO: DRE estruturada pelo plano de contas (grupos 1..7 + subtotais). Não
    # muda os totais acima — só organiza a origem. Tolerante a banco sem a
    # migração 132 (devolve estrutura vazia).
    estrutura = _dre_estrutura(pool, conta_id, ini, fim, tot_r, resultado)
    return {"ano": ano, "mes": mes,
            "receitas_centavos": tot_r, "despesas_centavos": tot_d,
            "resultado_centavos": resultado, "margem_pct": margem,
            "top_receitas": receitas[:top], "top_despesas": despesas[:top],
            "a_definir_n": int(adef[0] or 0),
            "a_definir_centavos": int(adef[1] or 0),
            "estrutura": estrutura}


def _dre_estrutura(pool, conta_id: int, ini, fim, tot_receita: int,
                   resultado_legado: int) -> dict:
    """Monta a DRE estruturada pelo plano de contas: uma linha por grupo (1..7)
    com as contas analíticas por baixo, mais os subtotais Receita Líquida, Lucro
    Bruto e Resultado. Sinal por grupo: receita soma (+), o resto subtrai (–).

    Reconciliação garantida: o Resultado é sempre o resultado_legado (receita –
    despesa do mês); o que não casou com o plano de contas cai na linha
    'A classificar', pra nenhum centavo sumir da tela.

    Tolerante: se plano_contas ainda não existe (deploy/teste antes da migração
    132), devolve estrutura vazia — os totais legados seguem válidos."""
    from .plano_contas import GRUPOS_DRE
    g = {gr: 0 for gr in GRUPOS_DRE}
    contas = {gr: [] for gr in GRUPOS_DRE}
    sem_conta_n = 0
    try:
        with pool.connection() as c:
            rows = c.execute(
                """select p.grupo, p.codigo, p.nome, p.natureza, sum(l.valor_centavos)
                     from lancamentos l
                     join plano_contas p on p.id = l.plano_conta_id
                    where l.conta_id=%s and l.data >= %s and l.data < %s
                      and l.natureza='empresa'
                    group by p.grupo, p.codigo, p.nome, p.natureza
                    order by p.grupo, p.codigo""",
                (conta_id, ini, fim)).fetchall()
            sc = c.execute(
                """select count(*) from lancamentos
                    where conta_id=%s and data >= %s and data < %s
                      and natureza='empresa' and plano_conta_id is null""",
                (conta_id, ini, fim)).fetchone()
    except Exception:
        return {"linhas": [], "resultado_centavos": resultado_legado,
                "sem_conta_centavos": 0, "sem_conta_n": 0, "disponivel": False}
    sem_conta_n = int(sc[0] or 0) if sc else 0
    tem_receita = {gr: False for gr in GRUPOS_DRE}
    for grupo, codigo, nome, cta_natureza, val in rows:
        val = int(val or 0)
        # O SINAL VEM DA CONTA, NAO DO GRUPO.
        #
        # Era `GRUPOS_DRE[grupo]["papel"]`, e com isso o grupo 7 ("Nao Operacional")
        # subtraia tudo — porque so' tinha despesa: Distribuicao de Lucros,
        # Emprestimos, Imobilizado. Um aporte de socio (7.1.05, dinheiro ENTRANDO)
        # cairia negativo. Grupo nao operacional de verdade tem os dois lados.
        #
        # Preservador pro que ja' existe: nos grupos 1 a 6 a natureza de cada conta
        # ja' casa com o papel do grupo, entao nenhum sinal antigo muda.
        sinal = 1 if cta_natureza == "receita" else -1
        if sinal > 0:
            tem_receita[grupo] = True
        g[grupo] += sinal * val
        contas[grupo].append({"codigo": codigo, "nome": nome,
                              "valor_centavos": sinal * val})
    classificado = sum(g.values())
    # tudo que não casou com o plano (sem conta ou classificação inconsistente)
    a_classificar = resultado_legado - classificado

    def _margem(v):
        return round(100.0 * v / tot_receita, 1) if tot_receita else 0.0

    def _grupo(gr):
        meta = GRUPOS_DRE[gr]
        # o "(–)" so' vale pra grupo de mao unica. O 7 passa a ter conta dos dois
        # lados (aporte entra, distribuicao sai) — prefixar seria mentir na metade.
        um_lado_so = meta["papel"] != "receita" and not tem_receita[gr]
        nome = "(–) " + meta["nome"] if um_lado_so else meta["nome"]
        return {"tipo": "grupo", "grupo": gr, "chave": "grupo_%d" % gr,
                "nome": nome, "valor_centavos": g[gr], "contas": contas[gr]}

    receita_liquida = g[1] + g[2]
    lucro_bruto = receita_liquida + g[3]
    linhas = [
        _grupo(1), _grupo(2),
        {"tipo": "subtotal", "chave": "receita_liquida",
         "nome": "= Receita Líquida", "valor_centavos": receita_liquida},
        _grupo(3),
        {"tipo": "subtotal", "chave": "lucro_bruto", "nome": "= Lucro Bruto",
         "valor_centavos": lucro_bruto, "margem_pct": _margem(lucro_bruto)},
        _grupo(4), _grupo(5), _grupo(6), _grupo(7),
    ]
    if sem_conta_n or a_classificar:
        linhas.append({"tipo": "grupo", "chave": "a_classificar",
                       "nome": "A classificar (sem conta contábil)",
                       "valor_centavos": a_classificar, "n": sem_conta_n,
                       "contas": []})
    linhas.append({"tipo": "total", "chave": "resultado",
                   "nome": "= Resultado do Mês", "valor_centavos": resultado_legado,
                   "margem_pct": _margem(resultado_legado)})
    return {"linhas": linhas, "resultado_centavos": resultado_legado,
            "receita_liquida_centavos": receita_liquida,
            "lucro_bruto_centavos": lucro_bruto,
            "sem_conta_centavos": a_classificar, "sem_conta_n": sem_conta_n,
            "disponivel": True}


def dre_por_centro(pool, conta_id: int, ano: int, mes: int) -> dict:
    """DRE estruturada com uma COLUNA por centro de custo (o corte 'por centro'
    do painel). Só entra o que está classificado no plano de contas E tem centro
    (lançamentos sem centro caem na coluna 'Sem centro'). Cada linha traz o valor
    em cada centro + o total. Tolerante a banco sem a migração 132."""
    from .plano_contas import GRUPOS_DRE, listar_centros
    ini = date(ano, mes, 1)
    fim = _mes_seguinte(ini)
    try:
        with pool.connection() as c:
            rows = c.execute(
                """select p.grupo, l.centro_custo_id, p.natureza, sum(l.valor_centavos)
                     from lancamentos l
                     join plano_contas p on p.id = l.plano_conta_id
                    where l.conta_id=%s and l.data >= %s and l.data < %s
                      and l.natureza='empresa'
                    group by p.grupo, l.centro_custo_id, p.natureza""",
                (conta_id, ini, fim)).fetchall()
    except Exception:
        return {"ano": ano, "mes": mes, "centros": [], "linhas": [],
                "disponivel": False}
    # ordem das colunas segue a tela de Centros (ordem, nome)
    centros_ord = listar_centros(pool, conta_id, incluir_inativos=True)
    nomes = {ct["id"]: ct["nome"] for ct in centros_ord}
    idx = {ct["id"]: i for i, ct in enumerate(centros_ord)}
    # acumula por (grupo, coluna); coluna = id do centro ou 'sem'
    g = {gr: {} for gr in GRUPOS_DRE}
    presentes, tem_sem = [], False
    for grupo, centro_id, cta_natureza, val in rows:
        val = int(val or 0)
        # mesmo motivo do dre_do_mes: o sinal e' da CONTA. Este ponto ficou pra
        # tras na primeira passada e o teste pegou — sem ele o aporte apareceria
        # certo na DRE e negativo no detalhamento por centro de custo, que e' o
        # tipo de divergencia que ninguem cruza ate' alguem reclamar do numero.
        sinal = 1 if cta_natureza == "receita" else -1
        col = centro_id if centro_id is not None else "sem"
        g[grupo][col] = g[grupo].get(col, 0) + sinal * val
        if centro_id is None:
            tem_sem = True
        elif centro_id not in presentes:
            presentes.append(centro_id)
    presentes.sort(key=lambda cid: (idx.get(cid, 1 << 30), nomes.get(cid, "")))
    colunas = [{"key": cid, "nome": nomes.get(cid, "#%d" % cid)} for cid in presentes]
    if tem_sem:
        colunas.append({"key": "sem", "nome": "Sem centro"})
    keys = [col["key"] for col in colunas]

    def _linha(nome, grupos, tipo="grupo"):
        por = {k: sum(g[gr].get(k, 0) for gr in grupos) for k in keys}
        return {"tipo": tipo, "nome": nome, "por_centro": por,
                "total_centavos": sum(por.values())}

    linhas = [
        _linha(GRUPOS_DRE[1]["nome"], [1]),
        _linha("(–) " + GRUPOS_DRE[2]["nome"], [2]),
        _linha("= Receita Líquida", [1, 2], tipo="subtotal"),
        _linha("(–) " + GRUPOS_DRE[3]["nome"], [3]),
        _linha("= Lucro Bruto", [1, 2, 3], tipo="subtotal"),
        _linha("(–) " + GRUPOS_DRE[4]["nome"], [4]),
        _linha("(–) " + GRUPOS_DRE[5]["nome"], [5]),
        _linha("(–) " + GRUPOS_DRE[6]["nome"], [6]),
        _linha("(–) " + GRUPOS_DRE[7]["nome"], [7]),
        _linha("= Resultado do Mês", [1, 2, 3, 4, 5, 6, 7], tipo="total"),
    ]
    return {"ano": ano, "mes": mes, "centros": colunas, "linhas": linhas,
            "disponivel": True}


def csv_contador(pool, conta_id: int, ano: int, mes: int) -> str:
    """Relatório do mês pro contador: todos os lançamentos + títulos abertos.

    CSV separado por ';' (Excel BR), valores em reais com vírgula.
    """
    def brl(cent: int) -> str:
        return f"{cent/100:.2f}".replace(".", ",")

    ini = date(ano, mes, 1)
    fim = _mes_seguinte(ini)
    with pool.connection() as c:
        lanc = c.execute(
            """select data, tipo, categoria, descricao, valor_centavos, origem,
                      natureza
                 from lancamentos
                where conta_id=%s and data >= %s and data < %s
                order by data, id""",
            (conta_id, ini, fim),
        ).fetchall()
    linhas = ["data;tipo;categoria;descricao;valor;origem;natureza"]
    for d, t, cat, desc, v, orig, nat in lanc:
        desc = (desc or "").replace(";", ",").replace("\n", " ")
        linhas.append(
            f"{d};{t};{cat};{desc};{brl(int(v or 0))};{orig};{nat or 'a definir'}")

    abertos = listar_titulos(pool, conta_id, status="aberto")
    linhas.append("")
    linhas.append("TITULOS EM ABERTO")
    linhas.append("vencimento;tipo;descricao;contraparte;valor;atrasado")
    for t in abertos:
        desc = (t["descricao"] or "").replace(";", ",")
        linhas.append(
            f"{t['vencimento']};{t['tipo']};{desc};{t['contraparte']};"
            f"{brl(t['valor_centavos'])};{'sim' if t['atrasado'] else 'nao'}")
    return "\n".join(linhas) + "\n"


# ---------------------------------------------------------------------------
# Dados cadastrais da empresa (cadastro pós-upgrade PF->PJ)
# ---------------------------------------------------------------------------
def o_que_vende(pool, conta_id: int) -> dict:
    """O que a conta vende: {produto: bool, servico: bool}.

    A verdade é o par de colunas contas.vende_produto/vende_servico (decisão do
    cliente, parte A+). Enquanto NULL (não definido ainda), cai no padrão do
    nicho da conta. Nunca devolve os dois False.
    """
    from . import nichos as _n
    with pool.connection() as c:
        r = c.execute(
            """select ct.vende_produto, ct.vende_servico, n.slug
                 from contas ct
                 left join nichos n on n.id = ct.nicho_id
                where ct.id=%s""",
            (conta_id,),
        ).fetchone()
    slug = r[2] if r else None
    prod = r[0] if r and r[0] is not None else _n.vende_produto(slug)
    serv = r[1] if r and r[1] is not None else _n.vende_servico(slug)
    if not prod and not serv:  # segurança: nunca deixa os dois desligados
        prod = True
    return {"produto": bool(prod), "servico": bool(serv)}


def definir_o_que_vende(pool, conta_id: int, *, produto=None, servico=None) -> dict:
    """Liga/desliga produto e/ou servico pra conta (parte A+: destravar a aba).

    Passe só o que quer mudar (produto=True liga a aba Produtos, etc.). O que vier
    None mantém o valor atual. NUNCA deixa os dois desligados (ignora um desligar
    que zeraria tudo). Devolve o estado final {produto, servico}.
    """
    atual = o_que_vende(pool, conta_id)
    novo_prod = atual["produto"] if produto is None else bool(produto)
    novo_serv = atual["servico"] if servico is None else bool(servico)
    if not novo_prod and not novo_serv:  # não deixa a conta sem nada pra vender
        return atual
    with pool.connection() as c:
        c.execute(
            "update contas set vende_produto=%s, vende_servico=%s where id=%s",
            (novo_prod, novo_serv, conta_id),
        )
        c.commit()
    return {"produto": novo_prod, "servico": novo_serv}


def dados_empresa_completos(pool, conta_id: int) -> bool:
    """True se a empresa já tem os dados mínimos preenchidos (CNPJ + razão).

    Usado como gate da aba Empresa: enquanto False, a aba mostra só a tela de
    preenchimento. O mínimo é documento (CNPJ) com 14 dígitos e razao_social.
    """
    with pool.connection() as c:
        r = c.execute(
            "select documento, razao_social from contas where id=%s",
            (conta_id,),
        ).fetchone()
    if not r:
        return False
    doc = "".join(ch for ch in (r[0] or "") if ch.isdigit())
    razao = (r[1] or "").strip()
    return len(doc) == 14 and bool(razao)


def cadastro_pj_ok(pool, conta_id: int) -> bool:
    """Gate das abas PJ (Produtos/Servicos): dados da empresa completos OU ja e'
    fornecedor com razao/cnpj preenchidos (que agora vivem em contas)."""
    return dados_empresa_completos(pool, conta_id)


def obter_dados_empresa(pool, conta_id: int) -> dict:
    """Devolve os dados cadastrais atuais da empresa (pra pré-preencher a tela)."""
    with pool.connection() as c:
        r = c.execute(
            """select coalesce(ct.documento,''), coalesce(ct.razao_social,''),
                      coalesce(ct.nome_fantasia,''), coalesce(ct.endereco,''),
                      coalesce(ct.bairro,''), coalesce(ct.cep,''),
                      coalesce(ct.cidade,''), coalesce(ct.uf,''),
                      coalesce(ct.email_empresa,''), coalesce(ct.telefone,''),
                      coalesce(n.slug,''), coalesce(ct.cnae,'')
                 from contas ct
                 left join nichos n on n.id = ct.nicho_id
                where ct.id=%s""",
            (conta_id,),
        ).fetchone()
    if not r:
        return {"documento": "", "razao_social": "", "nome_fantasia": "",
                "endereco": "", "bairro": "", "cep": "", "cidade": "", "uf": "",
                "email_empresa": "", "telefone": "", "nicho": "", "cnae": ""}
    return {"documento": r[0], "razao_social": r[1], "nome_fantasia": r[2],
            "endereco": r[3], "bairro": r[4], "cep": r[5],
            "cidade": r[6], "uf": r[7], "email_empresa": r[8], "telefone": r[9],
            "nicho": r[10], "cnae": r[11]}


def salvar_dados_empresa(pool, conta_id: int, *, documento: str = "",
                         razao_social: str = "", nome_fantasia: str = "",
                         endereco: str = "", bairro: str = "", cep: str = "",
                         cidade: str = "", uf: str = "", email_empresa: str = "",
                         telefone: str = "", nicho: str = "",
                         cnae: str = "") -> tuple[bool, str]:
    """Grava os dados da empresa na conta. Valida CNPJ (14 dígitos) e razão.

    Retorna (ok, mensagem). Multi-tenant: grava só na própria conta.
    """
    doc = "".join(ch for ch in (documento or "") if ch.isdigit())
    razao = (razao_social or "").strip()
    if len(doc) != 14:
        return False, "CNPJ inválido — informe os 14 dígitos."
    if not razao:
        return False, "Informe a razão social."
    fantasia = (nome_fantasia or "").strip() or None
    end = (endereco or "").strip() or None
    bai = (bairro or "").strip() or None
    _cep = "".join(ch for ch in (cep or "") if ch.isdigit()) or None
    cid = (cidade or "").strip() or None
    est = (uf or "").strip().upper()[:2] or None
    mail = (email_empresa or "").strip().lower() or None
    tel = (telefone or "").strip() or None
    nicho_slug = (nicho or "").strip().lower() or None
    cnae_val = (cnae or "").strip()[:180] or None
    with pool.connection() as c:
        # CNAE: guarda a atividade crua da Receita (não sobrescreve com vazio).
        if cnae_val:
            c.execute("update contas set cnae=%s where id=%s", (cnae_val, conta_id))
        # resolve o slug do nicho -> nicho_id (FK). Se nao achar, deixa como esta'.
        nicho_id = None
        if nicho_slug:
            nr = c.execute("select id from nichos where slug=%s and ativo",
                           (nicho_slug,)).fetchone()
            nicho_id = nr[0] if nr else None
        if nicho_id is not None:
            c.execute(
                """update contas
                      set documento=%s, razao_social=%s, nome_fantasia=%s,
                          endereco=%s, bairro=%s, cep=%s, cidade=%s, uf=%s,
                          email_empresa=%s, telefone=%s, nicho_id=%s
                    where id=%s""",
                (doc, razao, fantasia, end, bai, _cep, cid, est, mail, tel,
                 nicho_id, conta_id),
            )
            # inicializa o que a conta vende pelo padrão do nicho, SEM sobrescrever
            # uma escolha já feita pelo cliente (coalesce mantém o valor atual).
            from .nichos import vende_produto as _vp, vende_servico as _vs
            c.execute(
                """update contas
                      set vende_produto = coalesce(vende_produto, %s),
                          vende_servico = coalesce(vende_servico, %s)
                    where id=%s""",
                (_vp(nicho_slug), _vs(nicho_slug), conta_id),
            )
        else:
            c.execute(
                """update contas
                      set documento=%s, razao_social=%s, nome_fantasia=%s,
                          endereco=%s, bairro=%s, cep=%s, cidade=%s, uf=%s,
                          email_empresa=%s, telefone=%s
                    where id=%s""",
                (doc, razao, fantasia, end, bai, _cep, cid, est, mail, tel, conta_id),
            )
        c.commit()
    return True, "Dados da empresa salvos."


def _cor_valida(cor: str) -> str | None:
    """Aceita só cor hex (#rgb ou #rrggbb); qualquer outra coisa vira None."""
    c = (cor or "").strip().lower()
    if not c:
        return None
    if c[0] != "#":
        c = "#" + c
    corpo = c[1:]
    if len(corpo) in (3, 6) and all(ch in "0123456789abcdef" for ch in corpo):
        return c
    return None


def obter_identidade(pool, conta_id: int) -> dict:
    """Identidade visual/marca da conta (compartilhada por toda empresa PJ, não só
    fornecedor): logo, capa, cor da marca, frase, sobre e WhatsApp público."""
    with pool.connection() as c:
        r = c.execute(
            """select logo_url, banner_url, coalesce(banner_cor,''),
                      coalesce(bio,''), coalesce(sobre,''), coalesce(whatsapp_loja,'')
                 from contas where id=%s""",
            (conta_id,)).fetchone()
    if not r:
        return {"logo_url": None, "banner_url": None, "banner_cor": "",
                "bio": "", "sobre": "", "whatsapp_loja": ""}
    return {"logo_url": r[0], "banner_url": r[1], "banner_cor": r[2],
            "bio": r[3], "sobre": r[4], "whatsapp_loja": r[5]}


def salvar_identidade(pool, conta_id: int, *, bio: str = "", sobre: str = "",
                      whatsapp_loja: str = "", banner_cor: str = "") -> None:
    """Grava os campos de texto/cor da identidade (a logo e a capa entram por upload
    à parte). Vazio limpa o campo. Cor inválida é ignorada (fica None). Multi-tenant."""
    with pool.connection() as c:
        c.execute(
            """update contas set bio=%s, sobre=%s, whatsapp_loja=%s, banner_cor=%s
                where id=%s""",
            ((bio or "").strip()[:160] or None,
             (sobre or "").strip()[:600] or None,
             (whatsapp_loja or "").strip()[:40] or None,
             _cor_valida(banner_cor), conta_id))
        c.commit()


def marca_empresa(pool, conta_id: int) -> dict:
    """Kit de marca pros cabeçalhos (proposta, painel, holerite…): sempre devolve
    algo usável — a logo quando existe, senão iniciais sobre a cor da marca."""
    with pool.connection() as c:
        r = c.execute(
            """select coalesce(nullif(nome_fantasia,''), nullif(razao_social,''), nome),
                      logo_url, coalesce(banner_cor,'')
                 from contas where id=%s""",
            (conta_id,)).fetchone()
    from .marca import iniciais as _iniciais
    nome = (r[0] if r else "") or ""
    return {"nome": nome, "logo_url": (r[1] if r else None),
            "cor": (r[2] if r else "") or "#2f7d32", "iniciais": _iniciais(nome)}
