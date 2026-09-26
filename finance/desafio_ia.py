"""O desafio: a IA do número × a equipe, mês a mês (etapa 4 do vendedor IA).

O dono quer MEDIR: "a ideia é medir depois" (26/09/2026) e "a cada 30 dias do mês".
Então as mesmas medidas, POR LEAD RECEBIDO no mês, pra cada coluna — o dono da regra
por número (o zaq teste, atendido pela IA) e cada vendedor da equipe:

  * leads novos (criados no mês, com o vendedor dono do card);
  * 1ª resposta (mediana, em minutos) e respondidos em até 5 min — a 1ª mensagem do
    cliente na conversa de WhatsApp até a 1ª resposta da empresa, de gente OU da IA
    (a régua do Raio-X conta só gente: aqui a IA é justamente o que se mede);
  * qualificados (data da festa E número de convidados no card);
  * visitas agendadas (a régua única de `finance/visita.py`, pela data da visita);
  * leads com orçamento que chegou ao cliente, e contratos assinados;
  * o custo da IA por lead e por contrato (`ia_uso`, migração 394);
  * por que a IA chamou gente (`ia_avisos`) e quanto a equipe levou pra agir;
  * fora do horário comercial: quanto a equipe demora e quanto da entrada é ali.

LEITURA COM CUIDADO (o próprio painel diz): o tráfego da campanha do chip não é o
mesmo do chip principal. A comparação indica; não prova.

Só leitura. Mês = mês civil em Brasília.
"""
from __future__ import annotations

import statistics
from datetime import date, datetime, timedelta, timezone

from finance import agenda as ag
from finance import evento_lead as evl
from finance import visita as vis

_TZ = "America/Sao_Paulo"
META_MIN = 1          # meta da IA: responder em menos de 1 minuto
EM_MIN = 5            # "respondidos em até 5 min"
META_VISITA = 0.08    # meta do time: visita marcada em 8% dos leads


def mes_atual() -> str:
    return evl.mes_chave(ag.agora_brt().date())


def meses(ultimos: int = 6) -> list[str]:
    """Os últimos meses, o atual primeiro — as pílulas da tela."""
    d = ag.agora_brt().date().replace(day=1)
    out = []
    for _ in range(ultimos):
        out.append(evl.mes_chave(d))
        d = (d - timedelta(days=1)).replace(day=1)
    return out


def comercial(t: datetime) -> bool:
    """Seg–sáb, 8h–18h em Brasília: a mesma janela de `agente_visita.na_janela`."""
    loc = t.astimezone(ag.BRT)
    return loc.weekday() <= 5 and 8 <= loc.hour < 18


def _mediana(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 1) if xs else None


def _pct(n, d):
    return round(100 * n / d) if d else None


def colunas(pool, conta_id: int) -> list[dict]:
    """A IA (o dono de cada regra com a IA ligada) primeiro, depois a equipe ativa."""
    with pool.connection() as c:
        try:
            with c.transaction():
                ia = [r[0] for r in c.execute(
                    """select distinct membro_id from chip_regra
                        where conta_id=%s and ia_ligada and membro_id is not null""",
                    (conta_id,)).fetchall()]
        except Exception:  # noqa: BLE001 — banco sem a 388
            ia = []
        rows = c.execute(
            """select id, coalesce(nullif(nome,''), email, 'Sem nome') from membros
                where conta_id=%s and ativo and papel in ('vendedor','gestor','dono')
                order by nome""", (conta_id,)).fetchall()
    nomes = {r[0]: r[1] for r in rows}
    out = [{"id": m, "nome": nomes.get(m, "IA"), "ia": True} for m in ia]
    out += [{"id": m, "nome": n, "ia": False} for m, n in nomes.items() if m not in ia]
    return out


def _por_lead(pool, conta_id: int, membros: list[int], ini: date, fim: date) -> list[dict]:
    """Uma linha por lead recebido no mês: de quem é, e o que aconteceu com ele."""
    festa = vis.vende_festa(pool, conta_id)
    from web.painel_relatorios import _ORC_CHEGOU
    from finance.cockpit_dono import SQL_CT_VIVO
    sql = f"""
        with l as (
          select p.id, p.vendedor_id, p.criado_em, p.evento_em, p.evento_convidados,
                 p.orcamento_id
            from prospeccao p
           where p.conta_id=%s and p.vendedor_id = any(%s)
             and (p.criado_em at time zone '{_TZ}')::date >= %s
             and (p.criado_em at time zone '{_TZ}')::date < %s),
        pin as (
          select l.id lead, min(m.criado_em) t
            from l join conversas cv on cv.prospeccao_id = l.id and cv.conta_id = %s
                                    and cv.canal = 'whatsapp'
            join mensagens m on m.conversa_id = cv.id and m.direcao = 'in'
           group by l.id)
        select l.id, l.vendedor_id, pin.t,
               (select min(m.criado_em) from conversas cv
                  join mensagens m on m.conversa_id = cv.id
                 where cv.prospeccao_id = l.id and cv.conta_id = %s and cv.canal = 'whatsapp'
                   and m.direcao = 'out' and m.autor in ('humano','bot')
                   and m.criado_em >= pin.t) resp,
               (l.evento_em is not null and l.evento_convidados is not null) qualif,
               exists (select 1 from eventos_agenda e
                        where e.conta_id = %s and e.prospeccao_id = l.id
                          and {vis.sql_conta('e', festa=festa)}) visita,
               exists (select 1 from orcamentos o
                        where o.id = l.orcamento_id and o.conta_id = %s and {_ORC_CHEGOU}) orc,
               exists (select 1 from contratos c
                        where c.orcamento_id = l.orcamento_id and c.conta_id = %s
                          and {SQL_CT_VIVO}) contrato
          from l left join pin on pin.lead = l.id"""
    with pool.connection() as c:
        rows = c.execute(sql, (conta_id, membros, ini, fim, conta_id, conta_id, conta_id,
                               conta_id, conta_id)).fetchall()
    return [dict(zip(("id", "membro", "t_in", "t_resp", "qualif", "visita", "orc", "contrato"), r))
            for r in rows]


def _custo(pool, conta_id: int, ini: date, fim: date) -> int | None:
    try:
        with pool.connection() as c:
            r = c.execute(
                f"""select coalesce(sum(custo_centavos), 0) from ia_uso
                     where conta_id=%s and (criado_em at time zone '{_TZ}')::date >= %s
                       and (criado_em at time zone '{_TZ}')::date < %s""",
                (conta_id, ini, fim)).fetchone()
    except Exception:  # noqa: BLE001 — banco sem a 394
        return None
    return int(r[0] or 0)


MOTIVOS_ROTULO = {"visita": "Visita", "agenda": "Agenda / data ocupada", "pessoa": "Pediu uma pessoa",
                  "fora_da_base": "Fora da base", "reclamacao": "Reclamação",
                  "desconto": "Pediu desconto", "sinal": "Sinal"}


def _avisos(pool, conta_id: int, ini: date, fim: date) -> list[dict]:
    """Por que a IA chamou gente, e em quantos minutos a equipe respondeu o cliente
    depois do aviso (a 1ª mensagem de gente na conversa; sem ela, "ainda não")."""
    try:
        with pool.connection() as c:
            rows = c.execute(
                f"""select a.motivo,
                           (select extract(epoch from min(m.criado_em) - a.criado_em) / 60
                              from mensagens m where m.conversa_id = a.conversa_id
                               and m.direcao = 'out' and m.autor = 'humano'
                               and m.criado_em > a.criado_em) minutos
                      from ia_avisos a
                     where a.conta_id=%s and (a.criado_em at time zone '{_TZ}')::date >= %s
                       and (a.criado_em at time zone '{_TZ}')::date < %s""",
                (conta_id, ini, fim)).fetchall()
            comp = c.execute(
                f"""select count(*) from ia_orcamentos
                     where conta_id=%s and comprovante_em is not null
                       and (comprovante_em at time zone '{_TZ}')::date >= %s
                       and (comprovante_em at time zone '{_TZ}')::date < %s""",
                (conta_id, ini, fim)).fetchone()[0]
    except Exception:  # noqa: BLE001
        return []
    grupos: dict[str, list] = {}
    for motivo, minutos in rows:
        grupos.setdefault(motivo, []).append(float(minutos) if minutos is not None else None)
    out = [{"motivo": MOTIVOS_ROTULO.get(k, k), "n": len(v), "agiu": sum(1 for x in v if x is not None),
            "mediana_min": _mediana(v)} for k, v in sorted(grupos.items(), key=lambda kv: -len(kv[1]))]
    if comp:
        out.insert(0, {"motivo": "Comprovante chegou", "n": int(comp), "agiu": None,
                       "mediana_min": None})
    return out


def dados(pool, conta_id: int, mes: str | None = None) -> dict:
    """Tudo o que a tela do desafio mostra, pro mês `AAAA-MM` (o atual por padrão)."""
    mes = mes if evl.mes_valido(mes or "") else mes_atual()
    ini, fim = evl.mes_intervalo(mes)
    cols = colunas(pool, conta_id)
    linhas = _por_lead(pool, conta_id, [c["id"] for c in cols], ini, fim) if cols else []
    por = {c["id"]: [] for c in cols}
    for x in linhas:
        por.setdefault(x["membro"], []).append(x)
    res = []
    for col in cols:
        xs = por.get(col["id"], [])
        esperas = [(x["t_resp"] - x["t_in"]).total_seconds() / 60
                   for x in xs if x["t_in"] and x["t_resp"]]
        com_in = sum(1 for x in xs if x["t_in"])
        res.append({**col, "leads": len(xs),
                    "resp_mediana_min": _mediana(esperas),
                    "resp_5min_pct": _pct(sum(1 for e in esperas if e <= EM_MIN), com_in),
                    "qualif": sum(1 for x in xs if x["qualif"]),
                    "visitas": sum(1 for x in xs if x["visita"]),
                    "visitas_pct": _pct(sum(1 for x in xs if x["visita"]), len(xs)),
                    "orcamentos": sum(1 for x in xs if x["orc"]),
                    "contratos": sum(1 for x in xs if x["contrato"])})
    ia_cols = [r for r in res if r["ia"]]
    custo = _custo(pool, conta_id, ini, fim) if ia_cols else None
    leads_ia = sum(r["leads"] for r in ia_cols)
    contratos_ia = sum(r["contratos"] for r in ia_cols)
    # ONDE A IA TENDE A GANHAR: a equipe fora do horário comercial
    equipe = [x for x in linhas if x["membro"] not in {c["id"] for c in ia_cols}]
    fora = [x for x in equipe if x["t_in"] and not comercial(x["t_in"])]
    esp_fora = [(x["t_resp"] - x["t_in"]).total_seconds() / 60 for x in fora if x["t_resp"]]
    return {
        "mes": mes, "mes_rotulo": evl.mes_rotulo(mes), "meses": meses(),
        "colunas": res, "tem_ia": bool(ia_cols),
        "custo_centavos": custo,
        "custo_por_lead": (round(custo / leads_ia) if custo is not None and leads_ia else None),
        "custo_por_contrato": (round(custo / contratos_ia) if custo is not None and contratos_ia
                               else None),
        "avisos": _avisos(pool, conta_id, ini, fim) if ia_cols else [],
        "fora": {"leads_pct": _pct(len(fora), sum(1 for x in equipe if x["t_in"])),
                 "mediana_min": _mediana(esp_fora),
                 "em5_pct": _pct(sum(1 for e in esp_fora if e <= EM_MIN), len(fora))},
        "meta_min": META_MIN, "meta_visita_pct": round(META_VISITA * 100),
    }
