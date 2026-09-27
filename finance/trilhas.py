"""AS TRÊS TRILHAS DO FUNIL: vendedores, a IA do número e o resgate da IA.

Mockup docs/mockups/funil_tres_trilhas.html (versão 2), aprovado pelo dono em
27/09/2026 "com as recomendações". O quadro continua UM só, com as mesmas etapas; em
cima dele, só pra dono e gestor, a barra das trilhas diz o que cada uma está fazendo
e filtra o quadro. Na frente, a COLUNA RESGATE mostra quem está com o resgate em
andamento e de onde veio.

A COLUNA É UMA VISÃO. A etapa do lead não muda: o card mostra "etapa: Negociação" e
continua contando lá no relatório, no Raio-X e no teto da etapa. Mudar a etapa pra
"Resgate" apagaria em que pé ele estava — e o motor inteiro do funil lê a etapa.

DE QUEM É CADA LEAD, na ordem:
  * 'rsg' — está com a IA pelo resgate (`resgate_leads` ativo e o dono ainda é o
    membro do resgate). Em andamento enquanto a IA chama e o cliente não respondeu;
    respondeu, o card volta pra coluna da etapa com o selo "veio do Resgate".
  * 'ia'  — o dono é a IA (`chip_regra.membros_ia`): o contato novo do chip dela.
  * 'vend' — o resto: o lead de gente, cobrado pela esteira.

Só leitura, e tolerante: sem as tabelas (banco sem a 396/401/403), todo lead é de
vendedor e a barra não aparece.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

_log = logging.getLogger(__name__)

_BRT = timezone(timedelta(hours=-3))

TRILHAS = ("vend", "ia", "rsg")
ROTULO = {"vend": "Vendedores", "ia": "IA do número", "rsg": "Resgate da IA"}

#: "conversando" = a IA falou com o cliente há menos disto; mais que isso, com a
#: última fala dela, o cliente "sumiu" (e a IA insiste, se a chave estiver ligada)
CONVERSA_H = 72


def _primeiro(nome) -> str:
    n = (nome or "").strip().split()
    return n[0].capitalize() if n else ""


def _dm(dt) -> str:
    return dt.astimezone(_BRT).strftime("%d/%m") if dt else ""


def _em_dias(alvo: datetime | None, agora: datetime) -> str:
    if not alvo:
        return ""
    d = (alvo.astimezone(_BRT).date() - agora.astimezone(_BRT).date()).days
    return "hoje" if d <= 0 else ("amanhã" if d == 1 else f"em {d} dias")


def passo(r: dict, agora: datetime) -> str:
    """O próximo passo do resgate, em texto ("retomada 26/09 · 2º toque em 2 dias")."""
    from finance import resgate as rg
    ult = r.get("ultimo_envio_em")
    if not ult:
        return "na fila · sai hoje"
    if r.get("uma_vez"):
        return (f"chamado {_dm(ult)} · uma vez só · vira perdido "
                f"{_em_dias(ult + timedelta(days=rg.UMA_VEZ_DIAS), agora)}")
    t = int(r.get("toques") or 1)
    if t <= 1:
        return f"retomada {_dm(ult)} · 2º toque {_em_dias(ult + timedelta(days=rg.TOQUE_2_DIAS), agora)}"
    if t == 2:
        return (f"2º toque {_dm(ult)} · última chamada "
                f"{_em_dias(ult + timedelta(days=rg.TOQUE_INTERVALO_DIAS), agora)}")
    return (f"última chamada {_dm(ult)} · vira perdido "
            f"{_em_dias(ult + timedelta(days=rg.PERDIDO_DEPOIS_DIAS), agora)}")


def do_quadro(c, conta_id: int, cards: list[dict], agora: datetime | None = None) -> dict:
    """{lead_id: {...}} com a trilha e os selos de cada card do quadro. Uma consulta
    pra todos os cards, nenhuma por card. Tolerante: falhou, todo mundo é 'vend'."""
    from finance import chip_regra as _cr
    from finance import resgate as rg
    agora = agora or datetime.now(timezone.utc)
    ids = [cc["id"] for cc in cards]
    if not ids:
        return {}
    ia = _cr.membros_ia(c, conta_id)
    rsg: dict[int, dict] = {}
    try:
        with c.transaction():
            for (lid, ativo, estado, origem, antes, desde, toques, ult, linha, uma_vez,
                 membro) in c.execute(
                    """select r.prospeccao_id, r.ativo, r.estado, r.origem,
                              coalesce(nullif(m.nome,''), m.email), r.parado_desde,
                              r.toques, r.ultimo_envio_em, r.resumo_linha, r.uma_vez,
                              r.membro_id
                         from resgate_leads r
                         left join membros m on m.id = r.vendedor_antes and m.conta_id = r.conta_id
                        where r.conta_id=%s and r.prospeccao_id = any(%s)""",
                    (conta_id, ids)).fetchall():
                rsg[lid] = {"ativo": ativo, "estado": estado, "origem": origem, "era": _primeiro(antes),
                            "parado_desde": desde, "toques": toques, "ultimo_envio_em": ult,
                            "resumo_linha": linha or "", "uma_vez": uma_vez, "membro": membro}
    except Exception:  # noqa: BLE001 — banco sem a 396/403
        rsg = {}
    # a conversa da IA: alguém da equipe assumiu (agente desligado, pendente)?
    pausada: set[int] = set()
    ids_ia = [cc["id"] for cc in cards if cc.get("vendedor_id") in ia]
    if ids_ia:
        try:
            with c.transaction():
                pausada = {r[0] for r in c.execute(
                    """select distinct on (cv.prospeccao_id) cv.prospeccao_id,
                              coalesce(cv.agente_ativo,false), coalesce(cv.status,'')
                         from conversas cv
                        where cv.conta_id=%s and cv.prospeccao_id = any(%s) and cv.canal='whatsapp'
                        order by cv.prospeccao_id, cv.ultima_msg_em desc nulls last, cv.id desc""",
                    (conta_id, ids_ia)).fetchall() if not r[1] and r[2] == "pendente"}
        except Exception:  # noqa: BLE001
            pausada = set()
    out = {}
    for cc in cards:
        lid, vend = cc["id"], cc.get("vendedor_id")
        r = rsg.get(lid)
        t = {"trilha": "vend", "andamento": False, "veio": False, "gente": False}
        if r and r["ativo"] and vend == r["membro"]:
            t["trilha"] = "rsg"
            # chamado e sem resposta ainda — inclusive o perdido chamado uma vez só
            t["andamento"] = r["estado"] == "chamado"
        elif vend in ia:
            t["trilha"] = "ia"
            t["gente"] = lid in pausada
        if r:
            dias = max(1, (agora - r["parado_desde"]).days) if r["parado_desde"] else None
            t.update({
                "origem_txt": rg.ORIGENS.get(r["origem"] or "", "veio do Resgate"),
                "era": r["era"], "parado_dias": dias,
                "passo": passo(r, agora) if t["andamento"] else "",
                "resumo": r["resumo_linha"],
            })
            # o selo "veio do Resgate" no card que saiu da coluna (respondeu, virou
            # perdido, ou voltou pro vendedor)
            t["veio"] = not t["andamento"]
        out[lid] = t
    return out


def barra(c, conta_id: int, cards: list[dict], tri: dict, agora: datetime | None = None) -> dict | None:
    """Os números da barra, sobre os cards do quadro (com os filtros de agora, menos
    o da trilha). None quando a conta não tem IA nem resgate — aí a barra some e o
    quadro fica como sempre foi (a ZAQ, com um chip só)."""
    from finance import chip_regra as _cr
    from finance import resgate as rg
    agora = agora or datetime.now(timezone.utc)
    ia = _cr.membros_ia(c, conta_id)
    try:
        cfg_rg = rg.config(c, conta_id)
    except Exception:  # noqa: BLE001
        cfg_rg = {"modo": "off"}
    if not ia and cfg_rg.get("modo", "off") == "off":
        return None
    abertos = {k: 0 for k in TRILHAS}
    ia_conv = ia_sumido = ia_gente = rsg_and = 0
    limite = agora - timedelta(hours=CONVERSA_H)
    for cc in cards:
        t = (tri.get(cc["id"]) or {}).get("trilha", "vend")
        if cc.get("status") != "perdido":
            abertos[t] += 1
        if t == "ia":
            ult = cc.get("ult") or {}
            if (tri.get(cc["id"]) or {}).get("gente"):
                ia_gente += 1
            elif ult.get("em") and ult["em"] >= limite:
                ia_conv += 1
            elif ult.get("minha"):
                ia_sumido += 1
        if t == "rsg" and (tri.get(cc["id"]) or {}).get("andamento"):
            rsg_and += 1
    out = {"abertos": abertos, "todas": sum(abertos.values()),
           "ia": {"conversando": ia_conv, "sumido": ia_sumido, "gente": ia_gente,
                  "nome": "", "insiste": False},
           "rsg": {"modo": cfg_rg.get("modo", "off"), "pausado": bool(cfg_rg.get("pausado_em")),
                   "andamento": rsg_and, "fila": None, "hoje": 0,
                   "teto": int(cfg_rg.get("teto_dia") or 20), "responderam": 0},
           "vend": {"esteira": None, "tratados": None}}
    # o nome do membro IA e se ele insiste (a chave da 401)
    try:
        with c.transaction():
            r = c.execute("""select coalesce(nullif(m.nome,''), m.email), bool_or(cr.ia_insiste)
                               from chip_regra cr join membros m on m.id = cr.membro_id
                              where cr.conta_id=%s and cr.ia_ligada and cr.ativa
                              group by 1 limit 1""", (conta_id,)).fetchone()
        if r:
            out["ia"]["nome"], out["ia"]["insiste"] = r[0] or "", bool(r[1])
    except Exception:  # noqa: BLE001
        pass
    # o resgate: a fila, o que saiu hoje e quem respondeu
    if cfg_rg.get("modo") in ("ensaio", "ligado"):
        try:
            out["rsg"]["fila"] = len(rg.fila(c, conta_id, cfg_rg, agora))
            with c.transaction():
                out["rsg"]["hoje"] = rg._contagem_hoje(c, conta_id, rg.ENVIOS, agora)
                out["rsg"]["responderam"] = c.execute(
                    "select count(*) from resgate_leads where conta_id=%s and ativo "
                    "and estado='respondeu'", (conta_id,)).fetchone()[0]
        except Exception:  # noqa: BLE001
            _log.info("trilhas: sem os números do resgate (conta=%s)", conta_id, exc_info=True)
    # a esteira: quantos estão nela e quantos foram tratados hoje
    try:
        from finance import esteira as _est
        with c.transaction():
            # a coluna direto, e não `esteira.config`: aquele semeia a linha da régua,
            # e abrir o quadro não escreve nada
            m = c.execute("select coalesce(esteira_modo,'off') from funil_regua where conta_id=%s",
                          (conta_id,)).fetchone()
            if m and m[0] != "off":
                r = _est.resumo(c, conta_id, None, agora, desde=_est._inicio_do_dia(agora))
                out["vend"] = {"esteira": r["na_esteira"], "tratados": r["tratou"]}
    except Exception:  # noqa: BLE001
        pass
    return out


def detalhe(c, conta_id: int, trilha: str, cards: list[dict], tri: dict) -> dict:
    """O painel embaixo do quadro, quando uma trilha está escolhida."""
    if trilha == "vend":
        linhas = []
        try:
            from finance import chip_regra as _cr
            ia = _cr.membros_ia(c, conta_id)
            with c.transaction():
                linhas = [{"nome": _primeiro(n), "n": k} for mid, n, k in c.execute(
                    """select e.membro_id, coalesce(nullif(m.nome,''), m.email), count(*)
                         from follow_up_esteira e join membros m on m.id = e.membro_id
                        where e.conta_id=%s and e.resolvido_em is null and e.fechado_em is null
                        group by 1, 2 order by 3 desc""", (conta_id,)).fetchall() if mid not in ia]
        except Exception:  # noqa: BLE001
            linhas = []
        return {"linhas": linhas, "rotulo": "na esteira agora, por vendedor"}
    if trilha == "rsg":
        from finance import resgate as rg
        cont: dict[str, int] = {}
        for cc in cards:
            t = tri.get(cc["id"]) or {}
            if t.get("trilha") == "rsg":
                o = t.get("origem_txt") or "veio do Resgate"
                cont[o] = cont.get(o, 0) + 1
        linhas = [{"nome": k[0].upper() + k[1:], "n": v} for k, v in
                  sorted(cont.items(), key=lambda kv: -kv[1])]
        return {"linhas": linhas, "rotulo": "com a IA pelo resgate, por origem",
                "origens": rg.ORIGENS}
    if trilha == "ia":
        return {"linhas": [], "rotulo": ""}
    return {}
