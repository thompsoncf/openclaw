"""O desafio: a IA do número × a equipe, mês a mês (etapa 4 do vendedor IA).

O dono quer MEDIR: "a ideia é medir depois" (26/09/2026) e "a cada 30 dias do mês".
Então as mesmas medidas, POR LEAD RECEBIDO no mês, pra cada coluna — o dono da regra
por número (o zaq teste, atendido pela IA) e cada vendedor da equipe:

  * leads novos (criados no mês, com o vendedor dono do card);
  * 1ª resposta (mediana, em minutos) e respondidos em até 5 min — a 1ª mensagem do
    cliente na conversa de WhatsApp até a 1ª resposta da empresa, de gente OU da IA
    (a régua do Raio-X conta só gente: aqui a IA é justamente o que se mede);
  * qualificados (data da festa E número de convidados no card);
  * leads do mês que marcaram visita (a régua única de `finance/visita.py`);
  * leads do mês com orçamento que chegou ao cliente, e com contrato assinado (pelo
    orçamento ligado ao card);
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


def _nomes(pool, conta_id: int, ids) -> dict:
    """Nome de cada membro — ATIVO OU NÃO: quem saiu em agosto continua na coluna de
    agosto, com os leads que recebeu."""
    ids = [i for i in set(ids) if i]
    if not ids:
        return {}
    with pool.connection() as c:
        return {r[0]: r[1] for r in c.execute(
            """select id, coalesce(nullif(nome,''), email, 'Sem nome') from membros
                where conta_id=%s and id = any(%s)""", (conta_id, ids)).fetchall()}


def _ia_ligada_hoje(pool, conta_id: int) -> bool:
    try:
        with pool.connection() as c:
            return bool(c.execute("""select 1 from chip_regra where conta_id=%s and ativa
                                      and ia_ligada and membro_id is not null limit 1""",
                                  (conta_id,)).fetchone())
    except Exception:  # noqa: BLE001
        return False


def _por_lead(pool, conta_id: int, ini: date, fim: date) -> list[dict]:
    """Uma linha por lead recebido no mês: de QUEM é e o que aconteceu com ele.

    DE QUEM: o lead que a regra por número DEU (`chip_regra_leads`) é da IA — mesmo
    que o dono da regra seja uma pessoa que também recebe lead do rodízio (esses são
    dele, do lado da equipe). O resto é do vendedor dono do card.

    A 1ª RESPOSTA: a 1ª mensagem do cliente → a 1ª resposta da empresa. No lead da IA
    vale a fala da IA (autor bot); no da equipe só vale gente — robô de campanha não é
    vendedor respondendo. O recado de fora do horário da IA não é resposta, e envio
    que falhou também não.

    Uma passada só: as conversas do mês entram por join (sem subconsulta por lead —
    `conversas` não tem índice por lead)."""
    festa = vis.vende_festa(pool, conta_id)
    from web.painel_relatorios import _ORC_CHEGOU
    from finance.cockpit_dono import SQL_CT_VIVO
    try:
        with pool.connection() as c:
            with c.transaction():
                c.execute("select 1 from chip_regra_leads limit 1")
            tem_regra = True
    except Exception:  # noqa: BLE001 — banco sem a 388
        tem_regra = False
    ia_sql = ("(select r.membro_id from chip_regra_leads r where r.prospeccao_id = p.id "
              "and r.conta_id = p.conta_id)" if tem_regra else "null::bigint")
    # O LEAD DO RESGATE (migração 396) chegou na IA parado, não novo: no desafio ele
    # continua na coluna de quem ERA o dono no mês em que entrou (a aba do resgate é
    # outra conta). Sem dono antes, fica fora — não vira coluna do membro IA.
    try:
        with pool.connection() as c:
            with c.transaction():
                c.execute("select 1 from resgate_leads limit 1")
        vend_sql = ("(case when exists (select 1 from resgate_leads rg where rg.prospeccao_id = p.id"
                    " and rg.conta_id = p.conta_id) then (select rg.vendedor_antes from resgate_leads rg"
                    " where rg.prospeccao_id = p.id and rg.conta_id = p.conta_id)"
                    " else p.vendedor_id end)")
    except Exception:  # noqa: BLE001 — banco sem a 396
        vend_sql = "p.vendedor_id"
    sql = f"""
        with l as (
          select p.id, {vend_sql} vendedor_id, {ia_sql} ia_membro, p.evento_em,
                 p.evento_convidados, p.orcamento_id
            from prospeccao p
           where p.conta_id=%s and {vend_sql} is not null
             and (p.criado_em at time zone '{_TZ}')::date >= %s
             and (p.criado_em at time zone '{_TZ}')::date < %s),
        cv as (
          select cv.id, cv.prospeccao_id lead from conversas cv join l on l.id = cv.prospeccao_id
           where cv.conta_id = %s and cv.canal = 'whatsapp'),
        pin as (
          select cv.lead, min(m.criado_em) t
            from cv join mensagens m on m.conversa_id = cv.id and m.direcao = 'in'
           group by cv.lead),
        pout as (
          select pin.lead,
                 min(m.criado_em) filter (where m.autor = 'humano') t_humano,
                 min(m.criado_em) filter (where m.autor in ('humano','bot')) t_todos
            from pin join cv on cv.lead = pin.lead
            join mensagens m on m.conversa_id = cv.id and m.direcao = 'out'
                            and m.criado_em >= pin.t
                            and coalesce(m.status, '') not in ('ia_fora', 'erro')
           group by pin.lead)
        select l.id, l.vendedor_id, l.ia_membro, pin.t,
               case when l.ia_membro is not null then pout.t_todos else pout.t_humano end,
               (l.evento_em is not null and l.evento_convidados is not null),
               exists (select 1 from eventos_agenda e
                        where e.conta_id = %s and e.prospeccao_id = l.id
                          and {vis.sql_conta('e', festa=festa)}),
               exists (select 1 from orcamentos o
                        where o.id = l.orcamento_id and o.conta_id = %s and {_ORC_CHEGOU}),
               exists (select 1 from contratos c
                        where c.orcamento_id = l.orcamento_id and c.conta_id = %s
                          and {SQL_CT_VIVO})
          from l left join pin on pin.lead = l.id left join pout on pout.lead = l.id"""
    with pool.connection() as c:
        rows = c.execute(sql, (conta_id, ini, fim, conta_id, conta_id, conta_id,
                               conta_id)).fetchall()
    return [dict(zip(("id", "vendedor", "ia_membro", "t_in", "t_resp", "qualif", "visita",
                      "orc", "contrato"), r)) for r in rows]


def _custo(pool, conta_id: int, ini: date, fim: date, leads_ia: list[int]) -> dict | None:
    """O gasto da IA no mês (toda chamada) e o dos LEADS DO MÊS dela (o que divide por
    lead e por contrato — follow-up de lead de outro mês não entra na conta)."""
    try:
        with pool.connection() as c:
            r = c.execute(
                f"""select coalesce(sum(custo_centavos), 0),
                           coalesce(sum(custo_centavos) filter (where prospeccao_id = any(%s)), 0),
                           count(*)
                      from ia_uso
                     where conta_id=%s and (criado_em at time zone '{_TZ}')::date >= %s
                       and (criado_em at time zone '{_TZ}')::date < %s""",
                (leads_ia, conta_id, ini, fim)).fetchone()
    except Exception:  # noqa: BLE001 — banco sem a 394
        return None
    return {"mes": int(r[0]), "dos_leads": int(r[1]), "chamadas": int(r[2])}


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


def _coluna(nome: str, ia: bool, mid, xs: list[dict]) -> dict:
    esperas = [(x["t_resp"] - x["t_in"]).total_seconds() / 60
               for x in xs if x["t_in"] and x["t_resp"]]
    com_in = sum(1 for x in xs if x["t_in"])
    vis_n = sum(1 for x in xs if x["visita"])
    return {"id": mid, "nome": nome, "ia": ia, "leads": len(xs),
            "resp_mediana_min": _mediana(esperas),
            "resp_5min_pct": _pct(sum(1 for e in esperas if e <= EM_MIN), com_in),
            "qualif": sum(1 for x in xs if x["qualif"]),
            "visitas": vis_n, "visitas_pct": _pct(vis_n, len(xs)),
            "orcamentos": sum(1 for x in xs if x["orc"]),
            "contratos": sum(1 for x in xs if x["contrato"])}


def dados(pool, conta_id: int, mes: str | None = None) -> dict:
    """Tudo o que a tela do desafio mostra, pro mês `AAAA-MM` (o atual por padrão).

    As COLUNAS saem dos leads do mês, não da configuração de hoje: a IA é quem
    recebeu lead pela regra naquele mês (desligar a IA hoje não apaga setembro), e a
    equipe é quem era dono de lead — inclusive quem já saiu."""
    mes = mes if evl.mes_valido(mes or "") else mes_atual()
    ini, fim = evl.mes_intervalo(mes)
    linhas = _por_lead(pool, conta_id, ini, fim)
    ia_x = [x for x in linhas if x["ia_membro"]]
    eq_x = [x for x in linhas if not x["ia_membro"]]
    nomes = _nomes(pool, conta_id, [x["ia_membro"] for x in ia_x] + [x["vendedor"] for x in eq_x])
    cols = []
    for mid in sorted({x["ia_membro"] for x in ia_x}):
        cols.append(_coluna(nomes.get(mid, "IA"), True, mid, [x for x in ia_x if x["ia_membro"] == mid]))
    for mid in sorted({x["vendedor"] for x in eq_x}, key=lambda m: nomes.get(m, "").lower()):
        cols.append(_coluna(nomes.get(mid, "Sem nome"), False, mid,
                            [x for x in eq_x if x["vendedor"] == mid]))
    ia_total = _coluna("IA", True, None, ia_x) if ia_x else None
    custo = _custo(pool, conta_id, ini, fim, [x["id"] for x in ia_x])
    # ONDE A IA TENDE A GANHAR: a equipe fora do horário comercial
    fora = [x for x in eq_x if x["t_in"] and not comercial(x["t_in"])]
    esp_fora = [(x["t_resp"] - x["t_in"]).total_seconds() / 60 for x in fora if x["t_resp"]]
    return {
        "mes": mes, "mes_rotulo": evl.mes_rotulo(mes), "meses": meses(),
        "colunas": cols, "ia": ia_total,
        "tem_ia": bool(ia_x) or _ia_ligada_hoje(pool, conta_id),
        "custo_centavos": custo["mes"] if custo and (custo["chamadas"] or ia_x) else None,
        "custo_por_lead": (round(custo["dos_leads"] / len(ia_x)) if custo and ia_x else None),
        "custo_por_contrato": (round(custo["dos_leads"] / ia_total["contratos"])
                               if custo and ia_total and ia_total["contratos"] else None),
        "avisos": _avisos(pool, conta_id, ini, fim),
        "fora": {"leads_pct": _pct(len(fora), sum(1 for x in eq_x if x["t_in"])),
                 "mediana_min": _mediana(esp_fora),
                 "em5_pct": _pct(sum(1 for e in esp_fora if e <= EM_MIN), len(fora))},
        "meta_min": META_MIN, "meta_visita_pct": round(META_VISITA * 100),
    }
