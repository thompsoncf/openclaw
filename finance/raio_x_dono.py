"""O Raio-X do dono: o placar do período com filtros, uma linha por vendedor, e
os blocos que o Zaq enriquece sozinho (Peça 3 do mockup raio_x_como_fica).

O QUE ESTE MÓDULO FAZ, E O QUE NÃO FAZ
Como finance/raio_x.py, só LÊ: `prospeccao`, `conversas`, `mensagens`,
`orcamentos`, `contratos`, `eventos_agenda`, `membros`. As duas colunas que ele
precisa e não existiam (`perda_motivo`, `origem_cliente`, migração 209) são
preenchidas pelas fichas e pelo "perdido" do app — este módulo só as agrega.

OS FILTROS são os cortes que fizeram diferença na análise da Prime (05/09/2026):
período, vendedor, tipo de festa (com "sem tipo" como filtro próprio, porque era
47%), mês da festa, dia da festa (sábado era 62% dos pedidos), faixa de
convidados, de onde veio o cliente, e a hora em que o lead chegou (comercial,
noite, fim de semana — é o que prova a escala de sábado e o plantão do agente).

Todo filtro é uma condição sobre `prospeccao` (alias `p`), montada UMA vez em
`_where` e reaproveitada em cada consulta; o período entra por consulta, na
coluna certa (o lead pelo `criado_em`, a proposta pelo `criado_em` dela, o
contrato pelo `assinado_em`, a visita pelo `inicio`).

TEMPO em America/Sao_Paulo, como o resto.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from finance.raio_x import (_TZ, ABERTOS, META_PRIMEIRA_MIN, _anterior, _mediana, _reais,  # noqa: F401
                            agora_brt, confianca, cor, fmt_min, janela, responda_hoje, sua_semana,
                            texto_confianca)

_log = logging.getLogger("finance.raio_x_dono")

from finance.raio_x_perfil import (MOTIVOS_TODOS, PORTES, chave_porte, familia_segmento, familias,  # noqa: F401
                                   perfil_da_conta, regex_da_familia, regex_do_porte, rotulo_motivo)

#: por que perdeu — a lista COMPLETA (check da 213); o perfil escolhe seis.
MOTIVOS_PERDA = MOTIVOS_TODOS
#: de onde o cliente veio, na palavra dele (check da 209)
ORIGENS = (
    ("whatsapp", "WhatsApp"),
    ("indicacao", "Indicação"),
    ("instagram", "Instagram"),
    ("manual", "Manual"),
    ("outro", "Outro"),
)
#: os tipos de festa que viram filtro; o resto é "outro", vazio é "sem tipo"
TIPOS_FESTA = ("Casamento", "15 anos", "Aniversário", "Formatura", "Corporativo")
#: faixas de convidados: chave, rótulo, mínimo, máximo (None = aberto)
FAIXAS_CONVIDADOS = (
    ("ate59", "até 59", None, 59),
    ("60a99", "60 a 99", 60, 99),
    ("100a149", "100 a 149", 100, 149),
    ("150a199", "150 a 199", 150, 199),
    ("200mais", "200 ou mais", 200, None),
)
HORAS = (("comercial", "comercial"), ("noite", "noite"), ("fds", "fim de semana"))
DIAS_FESTA = (("sabado", "sábado"), ("resto", "outros dias"))
PERIODOS = (("semana", "Semana"), ("mes", "Mês"), ("tudo", "Tudo"), ("datas", "Datas"))
_DOW = ("dom", "seg", "ter", "qua", "qui", "sex", "sáb")
_MESES = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")
#: horário comercial, pra separar a 1ª resposta e a hora de chegada
COMERCIAL = (8, 18)


def rotulo_origem(chave: str | None) -> str:
    return dict(ORIGENS).get(chave or "", "")


# ---------------------------------------------------------------- os filtros

def _data(s: str | None) -> date | None:
    try:
        return date.fromisoformat((s or "").strip()[:10]) if (s or "").strip() else None
    except ValueError:
        return None


def filtros(params, perfil: dict | None = None) -> dict:
    """Normaliza o que veio da URL. Valor fora da lista vira 'todos' (vazio), nunca
    erro: a tela sempre abre. O PERFIL diz quais filtros existem: um filtro de
    festa numa conta de mensalidade é ignorado mesmo que venha na URL."""
    g = params.get if hasattr(params, "get") else (lambda k, d="": d)
    permitidos = set((perfil or {}).get("filtros") or
                     ("periodo", "vendedor", "tipo", "mes", "dia", "conv", "origem", "hora"))
    periodo = g("periodo", "mes") or "mes"
    if periodo not in dict(PERIODOS):
        periodo = "mes"
    de, ate = _data(g("de", "")), _data(g("ate", ""))
    if periodo == "datas" and not (de and ate and de <= ate):
        periodo = "mes"
    vend = (g("vendedor", "") or "").strip()
    vend_id = int(vend) if vend.isdigit() else None
    tipo = (g("tipo", "") or "").strip()
    if tipo not in TIPOS_FESTA + ("outro", "sem"):
        tipo = ""
    mes = (g("mes", "") or "").strip()
    try:
        mes_ini = date.fromisoformat(mes + "-01") if len(mes) == 7 else None
    except ValueError:
        mes_ini = None
    dia = g("dia", "") if g("dia", "") in dict(DIAS_FESTA) else ""
    conv = g("conv", "") if g("conv", "") in {k for k, *_ in FAIXAS_CONVIDADOS} else ""
    origem = g("origem", "") if g("origem", "") in dict(ORIGENS) else ""
    hora = g("hora", "") if g("hora", "") in dict(HORAS) else ""
    # os do perfil recorrente
    segmento = g("segmento", "") if g("segmento", "") in dict(familias()) else ""
    porte = g("porte", "") if g("porte", "") in {k for k, *_ in PORTES} | {"sem"} else ""
    uf = (g("uf", "") or "").strip().upper()[:2]
    uf = uf if (len(uf) == 2 and uf.isalpha()) or uf == "" else ""
    servico = (g("servico", "") or "").strip()[:80]
    f = {"periodo": periodo, "de": de if periodo == "datas" else None,
         "ate": ate if periodo == "datas" else None, "vendedor": vend_id,
         "tipo": tipo, "mes": mes_ini.strftime("%Y-%m") if mes_ini else "", "dia": dia,
         "conv": conv, "origem": origem, "hora": hora,
         "segmento": segmento, "porte": porte, "uf": uf, "servico": servico}
    for k in ("vendedor", "tipo", "mes", "dia", "conv", "origem", "hora", "segmento", "porte", "uf", "servico"):
        if k not in permitidos:
            f[k] = None if k == "vendedor" else ""
    return f


def janela_f(f: dict, agora: datetime | None = None) -> tuple[datetime, datetime, str]:
    if f["periodo"] == "datas" and f["de"] and f["ate"]:
        ini = datetime.combine(f["de"], datetime.min.time(), tzinfo=_TZ)
        fim = datetime.combine(f["ate"] + timedelta(days=1), datetime.min.time(), tzinfo=_TZ)
        return ini, fim, f"{f['de']:%d/%m} a {f['ate']:%d/%m}"
    return janela(f["periodo"], agora)


_HORA_LOCAL = "(p.criado_em at time zone 'America/Sao_Paulo')"


def _where(f: dict) -> tuple[str, list]:
    """As condições de filtro sobre `prospeccao p`, sem o período (que é por
    consulta) e sem a conta (idem)."""
    conds, vals = [], []
    if f.get("vendedor"):
        conds.append("p.vendedor_id = %s"); vals.append(f["vendedor"])
    t = f.get("tipo")
    if t == "sem":
        conds.append("coalesce(p.evento_tipo, '') = ''")
    elif t == "outro":
        conds.append("coalesce(p.evento_tipo, '') <> '' and lower(p.evento_tipo) <> all(%s)")
        vals.append([x.lower() for x in TIPOS_FESTA])
    elif t:
        conds.append("lower(p.evento_tipo) = %s"); vals.append(t.lower())
    if f.get("mes"):
        m = date.fromisoformat(f["mes"] + "-01")
        prox = (m.replace(day=28) + timedelta(days=4)).replace(day=1)
        conds.append("p.evento_em >= %s and p.evento_em < %s"); vals += [m, prox]
    if f.get("dia") == "sabado":
        conds.append("extract(dow from p.evento_em) = 6")
    elif f.get("dia") == "resto":
        conds.append("p.evento_em is not null and extract(dow from p.evento_em) <> 6")
    if f.get("conv"):
        _, _, lo, hi = next(x for x in FAIXAS_CONVIDADOS if x[0] == f["conv"])
        if lo is not None:
            conds.append("p.evento_convidados >= %s"); vals.append(lo)
        if hi is not None:
            conds.append("p.evento_convidados <= %s"); vals.append(hi)
        if lo is None:
            conds.append("p.evento_convidados is not null")
    if f.get("origem"):
        conds.append("p.origem_cliente = %s"); vals.append(f["origem"])
    seg = f.get("segmento")
    if seg == "sem":
        conds.append("coalesce(p.segmento, '') = ''")
    elif seg == "outro":
        conds.append("coalesce(p.segmento, '') <> ''")
        from finance.raio_x_perfil import FAMILIAS_SEGMENTO as _FAM
        for k, _, _rx in _FAM:
            conds.append("p.segmento !~* %s"); vals.append(regex_da_familia(k))
    elif seg:
        conds.append("p.segmento ~* %s"); vals.append(regex_da_familia(seg))
    porte = f.get("porte")
    if porte == "sem":
        conds.append("coalesce(p.porte, '') = ''")
    elif porte:
        conds.append("p.porte ~* %s"); vals.append(regex_do_porte(porte))
    if f.get("uf"):
        conds.append("upper(p.uf) = %s"); vals.append(f["uf"])
    if f.get("servico"):
        conds.append("""exists (select 1 from orcamentos o2, jsonb_array_elements(coalesce(o2.itens, '[]'::jsonb)) i
                                 where o2.id = p.orcamento_id and i->>'nome' ilike %s)""")
        vals.append(f["servico"])
    h = f.get("hora")
    if h == "fds":
        conds.append(f"extract(dow from {_HORA_LOCAL}) in (0, 6)")
    elif h == "comercial":
        conds.append(f"extract(dow from {_HORA_LOCAL}) between 1 and 5 "
                     f"and extract(hour from {_HORA_LOCAL}) >= %s and extract(hour from {_HORA_LOCAL}) < %s")
        vals += list(COMERCIAL)
    elif h == "noite":
        conds.append(f"extract(dow from {_HORA_LOCAL}) between 1 and 5 "
                     f"and (extract(hour from {_HORA_LOCAL}) < %s or extract(hour from {_HORA_LOCAL}) >= %s)")
        vals += list(COMERCIAL)
    sql = (" and " + " and ".join(conds)) if conds else ""
    return sql, vals


def _faixa_hora(dt: datetime) -> str:
    b = dt.astimezone(_TZ)
    if b.weekday() >= 5:
        return "fds"
    return "comercial" if COMERCIAL[0] <= b.hour < COMERCIAL[1] else "noite"


# ---------------------------------------------------------------- o placar

def _placar(c, conta_id: int, w: str, wv: list, ini, fim, agora) -> dict:
    leads, com_data, sem_tipo = c.execute(f"""
        select count(*), count(*) filter (where p.evento_em is not null),
               count(*) filter (where coalesce(p.evento_tipo, '') = '')
          from prospeccao p
         where p.conta_id = %s and p.criado_em >= %s and p.criado_em < %s{w}""",
        [conta_id, ini, fim, *wv]).fetchone()
    pico = c.execute(f"""
        select extract(dow from {_HORA_LOCAL})::int, extract(hour from {_HORA_LOCAL})::int, count(*)
          from prospeccao p
         where p.conta_id = %s and p.criado_em >= %s and p.criado_em < %s{w}
         group by 1, 2 order by 3 desc, 1, 2 limit 1""", [conta_id, ini, fim, *wv]).fetchone()
    resp = c.execute(f"""
        with prim as (
          select p.criado_em as chegou,
                 min(ms.criado_em) filter (where ms.direcao = 'in') as pin,
                 min(ms.criado_em) filter (where ms.direcao = 'out' and ms.autor = 'humano') as pout
            from prospeccao p
            join conversas cv on cv.prospeccao_id = p.id
            join mensagens ms on ms.conversa_id = cv.id
           where p.conta_id = %s and p.criado_em >= %s and p.criado_em < %s{w}
           group by p.id, p.criado_em)
        select chegou, extract(epoch from pout - pin) / 60 from prim
         where pin is not null and pout > pin""", [conta_id, ini, fim, *wv]).fetchall()
    por_faixa: dict[str, list[float]] = {"comercial": [], "noite": [], "fds": []}
    for chegou, m in resp:
        por_faixa[_faixa_hora(chegou)].append(float(m))
    todos = [m for xs in por_faixa.values() for m in xs]
    enviadas, valor_env, rasc, mensal_env = c.execute(f"""
        select count(*) filter (where o.status <> 'rascunho'),
               coalesce(sum(o.primeiro_ano_centavos) filter (where o.status <> 'rascunho'), 0),
               count(*) filter (where o.status = 'rascunho'),
               coalesce(sum(o.mensal_centavos) filter (where o.status <> 'rascunho'), 0)
          from orcamentos o join prospeccao p on p.orcamento_id = o.id
         where p.conta_id = %s and o.criado_em >= %s and o.criado_em < %s{w}""",
        [conta_id, ini, fim, *wv]).fetchone()
    contratos, valor_ctr, mensal_ctr = c.execute(f"""
        select count(*), coalesce(sum(c.valor_centavos), 0), coalesce(sum(o.mensal_centavos), 0)
          from contratos c join orcamentos o on o.id = c.orcamento_id
          join prospeccao p on p.orcamento_id = o.id
         where c.conta_id = %s and c.status = 'assinado' and c.assinado_em >= %s and c.assinado_em < %s{w}""",
        [conta_id, ini, fim, *wv]).fetchone()
    sem_assinar = c.execute(f"""
        select count(*) from orcamentos o join prospeccao p on p.orcamento_id = o.id
         where p.conta_id = %s and o.aprovada_em is not null{w}
           and not exists (select 1 from contratos c where c.orcamento_id = o.id and c.status = 'assinado')""",
        [conta_id, *wv]).fetchone()[0]
    vis = c.execute(f"""
        select count(*) filter (where e.desfecho = 'realizado'),
               count(*) filter (where e.desfecho = 'nao_realizado'),
               count(*) filter (where e.desfecho is null)
          from eventos_agenda e join prospeccao p on p.id = e.prospeccao_id
         where e.conta_id = %s and e.tipo = 'empresa' and e.tipo_evento is null
           and coalesce(e.status, 'ativo') = 'ativo'
           and e.inicio >= %s and e.inicio < least(%s, now()){w}""",
        [conta_id, ini, fim, *wv]).fetchone()
    vis_ok, vis_nao, vis_sem = (int(x) for x in vis)
    dias = max(1, (min(fim, agora) - ini).days) if fim > ini else 1
    return {
        "leads": int(leads), "leads_com_data": int(com_data), "leads_sem_tipo": int(sem_tipo),
        "leads_por_dia": round(int(leads) / dias, 1),
        "pico": (f"{_DOW[int(pico[0])]} {int(pico[1])}h" if pico else ""),
        "primeira_min": _mediana(todos), "primeira_n": len(todos),
        "primeira_em_5": sum(1 for m in todos if m <= META_PRIMEIRA_MIN),
        "primeira_comercial": _mediana(por_faixa["comercial"]),
        "primeira_noite": _mediana(por_faixa["noite"] + por_faixa["fds"]),
        "propostas": int(enviadas), "propostas_valor": int(valor_env), "rascunhos": int(rasc),
        "propostas_mensal": int(mensal_env),
        "contratos": int(contratos), "contratos_valor": int(valor_ctr), "sem_assinar": int(sem_assinar),
        "contratos_mensal": int(mensal_ctr),
        "visitas_ok": vis_ok, "visitas_nao": vis_nao, "visitas_sem_resposta": vis_sem,
        "visitas_pct": (round(100 * vis_ok / (vis_ok + vis_nao)) if (vis_ok + vis_nao) else None),
        # abaixo de metade respondida a taxa é pouco confiável (regra do relatório do funil)
        "visitas_confiavel": (vis_ok + vis_nao) >= max(1, (vis_ok + vis_nao + vis_sem)) / 2 if (vis_ok + vis_nao + vis_sem) else True,
    }


# ---------------------------------------------------------------- os blocos

def _tipo_canonico(t: str | None) -> str:
    t = (t or "").strip()
    if not t:
        return "sem tipo"
    for k in TIPOS_FESTA:
        if t.lower() == k.lower():
            return k
    return "Outro"


def _demanda_agenda(c, conta_id: int, w: str, wv: list, hoje: date) -> list[dict]:
    m0 = hoje.replace(day=1)
    meses = []
    m = m0
    for _ in range(6):
        meses.append(m)
        m = (m.replace(day=28) + timedelta(days=4)).replace(day=1)
    fim = (meses[-1].replace(day=28) + timedelta(days=4)).replace(day=1)
    pedindo = dict(c.execute(f"""
        select to_char(p.evento_em, 'YYYY-MM'), count(*)
          from prospeccao p
         where p.conta_id = %s and p.status <> 'perdido' and p.evento_em >= %s and p.evento_em < %s{w}
         group by 1""", [conta_id, m0, fim, *wv]).fetchall())
    agenda = dict(c.execute("""
        select to_char(e.inicio at time zone 'America/Sao_Paulo', 'YYYY-MM'), count(*)
          from eventos_agenda e
         where e.conta_id = %s and e.tipo = 'empresa' and e.tipo_evento is not null
           and coalesce(e.status, 'ativo') = 'ativo' and e.inicio >= %s and e.inicio < %s
         group by 1""", [conta_id, m0, fim]).fetchall())
    out = []
    for m in meses:
        k = m.strftime("%Y-%m")
        out.append({"mes": k, "rotulo": _MESES[m.month - 1] + (f" {m:%y}" if m.year != hoje.year else ""),
                    "pedindo": int(pedindo.get(k, 0)), "agenda": int(agenda.get(k, 0))})
    return out


def _dia_festa(c, conta_id, w, wv, ini, fim) -> list[dict]:
    rows = dict(c.execute(f"""
        select extract(dow from p.evento_em)::int, count(*)
          from prospeccao p
         where p.conta_id = %s and p.evento_em is not null and p.criado_em >= %s and p.criado_em < %s{w}
         group by 1""", [conta_id, ini, fim, *wv]).fetchall())
    return [{"dow": d, "rotulo": _DOW[d], "n": int(rows.get(d, 0))} for d in range(7)]


def _tipos_ticket(c, conta_id, w, wv, ini, fim) -> list[dict]:
    rows = c.execute(f"""
        select p.evento_tipo, count(*),
               avg(o.primeiro_ano_centavos) filter (where o.status <> 'rascunho' and coalesce(o.primeiro_ano_centavos, 0) > 0),
               count(o.id) filter (where o.status <> 'rascunho')
          from prospeccao p left join orcamentos o on o.id = p.orcamento_id
         where p.conta_id = %s and p.criado_em >= %s and p.criado_em < %s{w}
         group by 1""", [conta_id, ini, fim, *wv]).fetchall()
    agg: dict[str, dict] = {}
    for t, n, media, n_orc in rows:
        k = _tipo_canonico(t)
        a = agg.setdefault(k, {"tipo": k, "n": 0, "soma": 0.0, "n_orc": 0})
        a["n"] += int(n)
        if media is not None:
            a["soma"] += float(media) * int(n_orc); a["n_orc"] += int(n_orc)
    out = []
    for a in agg.values():
        out.append({"tipo": a["tipo"], "n": a["n"], "n_orc": a["n_orc"],
                    "ticket_centavos": int(a["soma"] / a["n_orc"]) if a["n_orc"] else None})
    out.sort(key=lambda x: (x["tipo"] == "sem tipo", -(x["ticket_centavos"] or 0), -x["n"]))
    return out


def _ciclo(c, conta_id, w, wv, ini, fim) -> dict:
    lp = c.execute(f"""
        select coalesce(m.nome, '—'), extract(epoch from o.criado_em - p.criado_em) / 86400
          from orcamentos o join prospeccao p on p.orcamento_id = o.id
          left join membros m on m.id = p.vendedor_id
         where p.conta_id = %s and o.status <> 'rascunho' and o.criado_em >= %s and o.criado_em < %s{w}""",
        [conta_id, ini, fim, *wv]).fetchall()
    pc = c.execute(f"""
        select extract(epoch from c.assinado_em - o.criado_em) / 86400
          from contratos c join orcamentos o on o.id = c.orcamento_id
          join prospeccao p on p.orcamento_id = o.id
         where c.conta_id = %s and c.status = 'assinado' and c.assinado_em >= %s and c.assinado_em < %s{w}""",
        [conta_id, ini, fim, *wv]).fetchall()
    por_vend: dict[str, list[float]] = {}
    for nome, d in lp:
        por_vend.setdefault(nome, []).append(max(0.0, float(d)))
    todos = [max(0.0, float(r[1])) for r in lp]
    import statistics as _st
    return {
        "lead_proposta_dias": round(_st.median(todos), 1) if todos else None,
        "lead_proposta_n": len(todos),
        "por_vendedor": sorted(({"nome": n.split(" ")[0], "dias": round(_st.median(xs), 1), "n": len(xs)}
                                for n, xs in por_vend.items()), key=lambda x: x["dias"]),
        "proposta_contrato_dias": round(_st.median([max(0.0, float(r[0])) for r in pc]), 1) if pc else None,
        "proposta_contrato_n": len(pc),
    }


def _perdas(c, conta_id, w, wv, ini, fim, motivos=MOTIVOS_TODOS) -> dict:
    rows = c.execute(f"""
        select coalesce(p.perda_motivo, ''), count(*)
          from prospeccao p
         where p.conta_id = %s and p.status = 'perdido' and p.atualizado_em >= %s and p.atualizado_em < %s{w}
         group by 1""", [conta_id, ini, fim, *wv]).fetchall()
    cont = {k: int(n) for k, n in rows}
    itens = [{"chave": k, "rotulo": r, "n": cont.get(k, 0)} for k, r in motivos]
    # um motivo gravado que não está na lista deste perfil (a conta trocou de
    # nicho) não some: entra com o rótulo dele
    for k, n in cont.items():
        if k and k not in dict(motivos):
            itens.append({"chave": k, "rotulo": rotulo_motivo(k), "n": n})
    itens.sort(key=lambda x: -x["n"])
    return {"itens": itens, "sem_motivo": cont.get("", 0), "total": sum(cont.values())}


# ---------------------------------------------------------------- os blocos do recorrente

def _mrr(c, conta_id, w, wv, ini, fim) -> list[dict]:
    """Mensalidade proposta × fechada, por mês do período (MRR novo)."""
    prop = dict(c.execute(f"""
        select to_char(o.criado_em at time zone 'America/Sao_Paulo', 'YYYY-MM'), coalesce(sum(o.mensal_centavos), 0)
          from orcamentos o join prospeccao p on p.orcamento_id = o.id
         where p.conta_id = %s and o.status <> 'rascunho' and o.criado_em >= %s and o.criado_em < %s{w}
         group by 1""", [conta_id, ini, fim, *wv]).fetchall())
    fech = dict(c.execute(f"""
        select to_char(c.assinado_em at time zone 'America/Sao_Paulo', 'YYYY-MM'), coalesce(sum(o.mensal_centavos), 0)
          from contratos c join orcamentos o on o.id = c.orcamento_id
          join prospeccao p on p.orcamento_id = o.id
         where c.conta_id = %s and c.status = 'assinado' and c.assinado_em >= %s and c.assinado_em < %s{w}
         group by 1""", [conta_id, ini, fim, *wv]).fetchall())
    meses = []
    m = ini.date().replace(day=1)
    while m < fim.date():
        k = m.strftime("%Y-%m")
        meses.append({"mes": k, "rotulo": _MESES[m.month - 1], "proposta": int(prop.get(k, 0)), "fechada": int(fech.get(k, 0))})
        m = (m.replace(day=28) + timedelta(days=4)).replace(day=1)
    return meses


def _segmentos(c, conta_id, w, wv, ini, fim) -> list[dict]:
    """Quantos leads chegaram de cada família de segmento, e quantos fecharam."""
    rows = c.execute(f"""
        select p.segmento, count(*),
               count(*) filter (where p.status = 'ganho' or exists (
                   select 1 from contratos c join orcamentos o on o.id = c.orcamento_id
                    where o.id = p.orcamento_id and c.status = 'assinado'))
          from prospeccao p
         where p.conta_id = %s and p.criado_em >= %s and p.criado_em < %s{w}
         group by 1""", [conta_id, ini, fim, *wv]).fetchall()
    agg: dict[str, dict] = {}
    for seg, n, fechou in rows:
        k, r = familia_segmento(seg)
        a = agg.setdefault(k, {"chave": k, "rotulo": r, "n": 0, "fechou": 0})
        a["n"] += int(n); a["fechou"] += int(fechou)
    out = list(agg.values())
    out.sort(key=lambda x: (x["chave"] == "sem", -x["n"]))
    return out


def _servicos(c, conta_id, w, wv, ini, fim) -> dict:
    """O serviço mais proposto (dos itens do orçamento) e a mensalidade média
    por serviço. Sem proposta enviada no período, cai em tudo-que-já-foi-orçado,
    marcado."""
    def consulta(so_periodo: bool):
        cond = " and o.criado_em >= %s and o.criado_em < %s" if so_periodo else ""
        params = [conta_id, *([ini, fim] if so_periodo else []), *wv]
        return c.execute(f"""
            select i->>'nome', count(distinct o.id),
                   avg(nullif(coalesce((i->>'mensal_centavos')::numeric, (i->>'mensal')::numeric * 100), 0))
              from orcamentos o join prospeccao p on p.orcamento_id = o.id,
                   jsonb_array_elements(coalesce(o.itens, '[]'::jsonb)) i
             where p.conta_id = %s and o.status <> 'rascunho'{cond}{w}
             group by 1 order by 2 desc, 1 limit 8""", params).fetchall()
    rows = consulta(True)
    historico = False
    if not rows:
        rows, historico = consulta(False), True
    return {"itens": [{"nome": n, "n": int(q), "mensal_centavos": (int(m) if m is not None else None)} for n, q, m in rows],
            "historico": historico}


# ---------------------------------------------------------------- tudo junto

def dono(pool, conta_id: int, f: dict, agora: datetime | None = None, perfil: dict | None = None) -> dict:
    """O Raio-X do dono pra um conjunto de filtros. O PERFIL (finance/raio_x_perfil)
    decide quais blocos existem: os de festa só pra quem vende data, os de
    mensalidade só pra quem vende serviço recorrente. Tolerante bloco a bloco:
    um que falhar vira None e a tela diz isso, sem derrubar o resto."""
    if perfil is None:
        perfil = perfil_da_conta(pool, conta_id)
    a = agora_brt(agora)
    ini, fim, rot = janela_f(f, a)
    ant_ini, ant_fim = _anterior(ini, fim)
    w, wv = _where(f)
    blocos = set(perfil.get("blocos") or ())
    out = {"ini": ini, "fim": fim, "rotulo": rot, "filtros": f, "perfil": perfil, "placar": None, "anterior": None,
           "demanda_agenda": None, "dia_festa": None, "tipos": None, "ciclo": None, "perdas": None,
           "mrr": None, "segmentos": None, "servicos": None,
           "vendedores": [], "confianca": None}
    todos = (("placar", lambda: _placar(c, conta_id, w, wv, ini, fim, a)),
             ("anterior", lambda: _placar(c, conta_id, w, wv, ant_ini, ant_fim, a)),
             ("demanda_agenda", lambda: _demanda_agenda(c, conta_id, w, wv, a.date())),
             ("dia_festa", lambda: _dia_festa(c, conta_id, w, wv, ini, fim)),
             ("tipos", lambda: _tipos_ticket(c, conta_id, w, wv, ini, fim)),
             ("mrr", lambda: _mrr(c, conta_id, w, wv, ini, fim)),
             ("segmentos", lambda: _segmentos(c, conta_id, w, wv, ini, fim)),
             ("servicos", lambda: _servicos(c, conta_id, w, wv, ini, fim)),
             ("ciclo", lambda: _ciclo(c, conta_id, w, wv, ini, fim)),
             ("perdas", lambda: _perdas(c, conta_id, w, wv, ini, fim, perfil.get("motivos") or MOTIVOS_TODOS)))
    with pool.connection() as c:
        for k, fn in todos:
            if k not in ("placar", "anterior") and k not in blocos:
                continue
            try:
                with c.transaction():
                    out[k] = fn()
            except Exception as e:  # noqa: BLE001
                _log.warning("raio-x do dono: bloco %s falhou: %s: %s", k, type(e).__name__, e)
        try:
            vend = c.execute("""select id, nome from membros
                                 where conta_id = %s and papel = 'vendedor' and coalesce(ativo, true)
                                   and (%s::bigint is null or id = %s) order by nome""",
                             (conta_id, f.get("vendedor"), f.get("vendedor"))).fetchall()
        except Exception as e:  # noqa: BLE001
            _log.warning("raio-x do dono: vendedores: %s: %s", type(e).__name__, e)
            vend = []
    for mid, nome in vend:
        try:
            s = sua_semana(pool, conta_id, mid, ini, fim)
            h = responda_hoje(pool, conta_id, mid, a)
            out["vendedores"].append({"id": mid, "nome": nome, "primeiro_nome": (nome or "—").split(" ")[0],
                                      "semana": s, "hoje": h["n"]})
        except Exception as e:  # noqa: BLE001
            _log.warning("raio-x do dono: vendedor %s: %s: %s", mid, type(e).__name__, e)
    try:
        out["confianca"] = confianca(pool, conta_id, ini, fim, a)
    except Exception as e:  # noqa: BLE001
        _log.warning("raio-x do dono: confiança: %s: %s", type(e).__name__, e)
    return out


def delta(atual, anterior) -> dict | None:
    """Pro comparativo: {'n': diferença, 'pct': variação} ou None sem base."""
    if atual is None or anterior is None:
        return None
    d = atual - anterior
    return {"n": d, "pct": (round(100 * d / anterior) if anterior else None)}
