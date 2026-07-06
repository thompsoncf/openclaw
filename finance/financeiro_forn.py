"""Financeiro do fornecedor (modelo B: comissao sobre TODAS as vendas).

- Faturamento = cestas (cobrada/entregue) + avulsos pagos, no periodo.
- Comissao = faturamento * comissao_pct (incide sobre tudo).
- Recebido direto = avulsos offline (dinheiro/cartao/Pix na entrega) -- ja no caixa.
- Online = cestas (Asaas) + avulsos "pagar_agora".
- Repasse = online - comissao_total  (modelo B: a comissao do offline sai daqui;
  pode ficar NEGATIVO = fornecedor deve a plataforma).
- Pendente = avulso entregue e nao pago (a receber na mao).

Nao grava nada; so leitura. Sem dependencia de migracao nova.
"""
from __future__ import annotations
from datetime import date, timedelta

_OFFLINE = ("entrega_dinheiro", "entrega_cartao", "entrega_pix")

_PERIODOS = ("mes", "mes_passado", "30dias", "tudo")

# Ciclo de repasse: dia da semana em que a plataforma repassa (0=seg ... 6=dom).
# Repasse semanal -- casa com a cesta semanal. Ajuste aqui se mudar o ciclo.
DIA_REPASSE = 4  # sexta-feira

_DIAS_PT = ("segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo")


def _proximo_repasse(hoje: date | None = None) -> date:
    """Proxima data de repasse (proxima ocorrencia de DIA_REPASSE, hoje inclusive)."""
    hoje = hoje or date.today()
    dias = (DIA_REPASSE - hoje.weekday()) % 7
    return hoje + timedelta(days=dias)


def _label_repasse(d: date) -> str:
    return "%s, %02d/%02d" % (_DIAS_PT[d.weekday()], d.day, d.month)


def _range(periodo: str):
    hoje = date.today()
    if periodo == "mes_passado":
        prim = hoje.replace(day=1)
        ult = prim - timedelta(days=1)
        return ult.replace(day=1), ult
    if periodo == "30dias":
        return hoje - timedelta(days=30), hoje
    if periodo == "tudo":
        return None, None
    return hoje.replace(day=1), hoje  # mes (default)


def resumo_financeiro(pool, fornecedor_id: int, periodo: str = "mes") -> dict:
    if periodo not in _PERIODOS:
        periodo = "mes"
    de, ate = _range(periodo)
    with pool.connection() as c:
        r = c.execute(
            "select comissao_pct from contas where id = %s",
            (fornecedor_id,),
        ).fetchone()
        _raw = r[0] if r else None
        comissao_configurada = _raw is not None
        pct = float(_raw) if _raw is not None else 0.0

        # --- cestas realizadas (cobrada/entregue) -> online (Asaas) ---
        cc = ["cs.fornecedor_id = %s", "cs.status in ('cobrada','entregue')"]
        pc: list = [fornecedor_id]
        if de:
            cc.append("coalesce(cs.entregue_em::date, cs.data_entrega, cs.criada_em::date) >= %s")
            cc.append("coalesce(cs.entregue_em::date, cs.data_entrega, cs.criada_em::date) <= %s")
            pc += [de, ate]
        row = c.execute(
            "select coalesce(sum(preco_centavos),0), count(*) from cesta_semana cs where "
            + " and ".join(cc), tuple(pc)).fetchone()
        cestas_cent, cestas_qtd = int(row[0] or 0), int(row[1] or 0)

        # --- avulsos pagos, separando online x offline ---
        ca = ["ca.fornecedor_id = %s", "ca.pago = true"]
        pa: list = [fornecedor_id]
        if de:
            ca.append("ca.pago_em::date >= %s")
            ca.append("ca.pago_em::date <= %s")
            pa += [de, ate]
        rows = c.execute(
            "select coalesce(forma_pagamento,''), coalesce(sum(total_centavos),0), count(*) "
            "from carrinhos ca where " + " and ".join(ca) + " group by forma_pagamento",
            tuple(pa)).fetchall()
        av_off, av_on, av_qtd = 0, 0, 0
        for forma, cent, q in rows:
            cent, q = int(cent or 0), int(q or 0)
            av_qtd += q
            if forma in _OFFLINE:
                av_off += cent
            else:
                av_on += cent

        # --- pendente: avulso entregue-nao-pago + cestas confirmadas (a cobrar) ---
        rp = c.execute(
            "select coalesce(sum(total_centavos),0), count(*) from carrinhos ca "
            "where ca.fornecedor_id = %s and ca.status = 'entregue' and ca.pago = false",
            (fornecedor_id,)).fetchone()
        pend_av_cent, pend_av_qtd = int(rp[0] or 0), int(rp[1] or 0)
        rc = c.execute(
            "select coalesce(sum(preco_centavos),0), count(*) from cesta_semana cs "
            "where cs.fornecedor_id = %s and cs.status = 'confirmada'",
            (fornecedor_id,)).fetchone()
        pend_ce_cent, pend_ce_qtd = int(rc[0] or 0), int(rc[1] or 0)
        pendente_cent = pend_av_cent + pend_ce_cent
        pendente_qtd = pend_av_qtd + pend_ce_qtd

    online = cestas_cent + av_on
    offline = av_off
    faturamento = online + offline
    comissao = round(faturamento * pct / 100)
    liquido = faturamento - comissao
    repasse = online - comissao  # modelo B: comissao total sai do online

    return {
        "periodo": periodo, "comissao_pct": pct,
        "comissao_configurada": comissao_configurada,
        "proximo_repasse_label": _label_repasse(_proximo_repasse()),
        "faturamento_cent": faturamento,
        "comissao_cent": comissao,
        "liquido_cent": liquido,
        "recebido_direto_cent": offline,
        "online_cent": online,
        "repasse_cent": repasse,
        "repasse_negativo": repasse < 0,
        "pendente_cent": pendente_cent, "pendente_qtd": pendente_qtd,
        "pendente_avulso_cent": pend_av_cent, "pendente_cesta_cent": pend_ce_cent,
        "cestas_cent": cestas_cent, "cestas_qtd": cestas_qtd,
        "avulsos_cent": av_off + av_on, "avulsos_qtd": av_qtd,
        "qtd_pedidos": cestas_qtd + av_qtd,
    }


def extrato_financeiro(pool, fornecedor_id: int, periodo: str = "mes",
                       limit: int = 60) -> list[dict]:
    """Lista de pedidos do periodo (cesta + avulso), pro extrato. Cada um com
    valor, comissao, liquido, forma (online/direto) e status."""
    if periodo not in _PERIODOS:
        periodo = "mes"
    de, ate = _range(periodo)
    with pool.connection() as c:
        r = c.execute(
            "select coalesce(comissao_pct, 0) from contas where id = %s",
            (fornecedor_id,)).fetchone()
        pct = float(r[0]) if r else 0.0

        itens: list[dict] = []

        cc = ["cs.fornecedor_id = %s", "cs.status in ('cobrada','entregue','confirmada')"]
        pc: list = [fornecedor_id]
        if de:
            cc.append("coalesce(cs.entregue_em::date, cs.data_entrega, cs.criada_em::date) >= %s")
            cc.append("coalesce(cs.entregue_em::date, cs.data_entrega, cs.criada_em::date) <= %s")
            pc += [de, ate]
        for cid, nome, cent, status, quando in c.execute(
            "select cs.id, coalesce(cli.nome,'cliente'), cs.preco_centavos, cs.status, "
            "coalesce(cs.entregue_em::date, cs.data_entrega, cs.criada_em::date) "
            "from cesta_semana cs left join contas cli on cli.id = cs.cliente_id "
            "where " + " and ".join(cc) + " order by 5 desc limit %s",
            tuple(pc) + (limit,)).fetchall():
            cent = int(cent or 0)
            _pago = status in ("cobrada", "entregue")
            itens.append({
                "tipo": "cesta", "id": int(cid), "cliente_nome": nome,
                "valor_cent": cent, "comissao_cent": round(cent * pct / 100),
                "liquido_cent": cent - round(cent * pct / 100),
                "canal": "online", "pago": _pago, "pendente": not _pago,
                "markable": False,
                "data": quando.strftime("%d/%m") if hasattr(quando, "strftime") else "",
            })

        ca = ["ca.fornecedor_id = %s",
              "(ca.pago = true or ca.status = 'entregue')"]
        pa: list = [fornecedor_id]
        if de:
            ca.append("coalesce(ca.pago_em::date, ca.entregue_em::date) >= %s")
            ca.append("coalesce(ca.pago_em::date, ca.entregue_em::date) <= %s")
            pa += [de, ate]
        for aid, nome, cent, forma, pago, quando in c.execute(
            "select ca.id, coalesce(cli.nome,'cliente'), ca.total_centavos, "
            "coalesce(ca.forma_pagamento,''), ca.pago, "
            "coalesce(ca.pago_em::date, ca.entregue_em::date) "
            "from carrinhos ca left join contas cli on cli.id = ca.cliente_id "
            "where " + " and ".join(ca) + " order by 6 desc limit %s",
            tuple(pa) + (limit,)).fetchall():
            cent = int(cent or 0)
            canal = "direto" if forma in _OFFLINE else "online"
            pago = bool(pago)
            itens.append({
                "tipo": "avulso", "id": int(aid), "cliente_nome": nome,
                "valor_cent": cent, "comissao_cent": round(cent * pct / 100),
                "liquido_cent": cent - round(cent * pct / 100),
                "canal": canal, "pago": pago, "pendente": not pago,
                "markable": not pago,
                "data": quando.strftime("%d/%m") if hasattr(quando, "strftime") else "",
            })

    itens.sort(key=lambda x: x.get("data") or "", reverse=True)
    return itens[:limit]


# ---------- Livro de repasses (a plataforma repassa; o fornecedor ve) ----------

def total_repassado(pool, fornecedor_id: int) -> int:
    """Soma de tudo que ja foi repassado ao fornecedor. Defensivo (tabela pode
    nao existir ate a migracao 052 rodar)."""
    with pool.connection() as c:
        try:
            r = c.execute(
                "select coalesce(sum(valor_centavos), 0) from repasses "
                "where fornecedor_id = %s", (fornecedor_id,)).fetchone()
            return int(r[0] or 0)
        except Exception:
            c.rollback()
            return 0


def saldo_a_repassar(pool, fornecedor_id: int) -> int:
    """Saldo REAL a repassar agora = (online - comissao, TODO o periodo)
    menos o que ja foi repassado. Pode ser negativo."""
    r = resumo_financeiro(pool, fornecedor_id, "tudo")
    return int(r["repasse_cent"]) - total_repassado(pool, fornecedor_id)


def registrar_repasse(pool, fornecedor_id: int, valor_centavos: int,
                      observacao: str = "", criado_por: int | None = None) -> bool:
    """Registra um repasse feito pela plataforma. Defensivo."""
    try:
        valor_centavos = int(valor_centavos)
    except Exception:
        return False
    if valor_centavos <= 0:
        return False
    with pool.connection() as c:
        try:
            c.execute(
                "insert into repasses (fornecedor_id, valor_centavos, observacao, criado_por) "
                "values (%s, %s, %s, %s)",
                (fornecedor_id, valor_centavos,
                 (observacao or "").strip() or None, criado_por))
            c.commit()
            return True
        except Exception:
            c.rollback()
            return False


def listar_repasses(pool, fornecedor_id: int, limit: int = 50) -> list:
    """Historico de repasses do fornecedor (mais recente primeiro). Defensivo."""
    with pool.connection() as c:
        try:
            rows = c.execute(
                "select id, valor_centavos, data_repasse, coalesce(observacao, '') "
                "from repasses where fornecedor_id = %s "
                "order by data_repasse desc, id desc limit %s",
                (fornecedor_id, limit)).fetchall()
            return [{"id": int(r[0]), "valor_cent": int(r[1]),
                     "data": r[2].strftime("%d/%m/%Y") if hasattr(r[2], "strftime") else str(r[2]),
                     "obs": r[3]} for r in rows]
        except Exception:
            c.rollback()
            return []
