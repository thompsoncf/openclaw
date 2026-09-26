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
                            agora_brt, confianca, cor, fmt_espera, fmt_min, janela, responda_hoje,
                            sua_semana, texto_confianca)

_log = logging.getLogger("finance.raio_x_dono")

from finance.cockpit_dono import SQL_CT_VENDEDOR, SQL_CT_VIVO  # noqa: E402
from finance import visita as _vis  # noqa: E402

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


def _where_contrato(f: dict) -> tuple[str, list]:
    """Os mesmos filtros, mas sobre `contratos c` (24/09/2026).

    O contrato entrava no Raio-X por `join prospeccao` — o que feito direto pelo
    orçamento, sem lead, não existia: na Prime, setembro dizia 8 contratos e eram
    10. Agora o contrato é a base. O vendedor é o do contrato (quem fez o
    orçamento; na falta, o do lead — a régua do cockpit). Os filtros que são do
    LEAD (tipo de festa, origem, segmento...) viram "tem um lead assim": aí o
    contrato sem lead fica de fora, e é o certo — não se sabe o tipo da festa dele.
    """
    conds, vals = [], []
    w_lead, wv_lead = _where({**f, "vendedor": None})
    if w_lead:
        conds.append("exists (select 1 from prospeccao p where p.orcamento_id = c.orcamento_id "
                     "and p.conta_id = c.conta_id" + w_lead + ")")
        vals += wv_lead
    if f.get("vendedor"):
        conds.append(SQL_CT_VENDEDOR + " = %s"); vals.append(f["vendedor"])
    return ((" and " + " and ".join(conds)) if conds else ""), vals


def _where_visita(f: dict) -> tuple[str, list]:
    """Os mesmos filtros, sobre a visita `e` (24/09/2026), pela régua de
    `finance.visita`: a visita sem card conta, e o vendedor é o dono do card ou,
    sem card, quem marcou.

    Era `join prospeccao` com o `{w}` do lead colado atrás — a visita digitada na
    Agenda sem card não existia, e na Prime o Raio-X dava 3 visitas ao Pedro Yan
    onde o Relatório dava 4. Como em `_where_contrato`, os filtros que são do LEAD
    (tipo de festa, origem...) viram "tem um lead assim": aí a visita sem card fica
    de fora, e é o certo — não se sabe de que festa ela é."""
    conds, vals = [], []
    w_lead, wv_lead = _where({**f, "vendedor": None})
    if w_lead:
        conds.append("exists (select 1 from prospeccao p where p.id = e.prospeccao_id "
                     "and p.conta_id = e.conta_id" + w_lead + ")")
        vals += wv_lead
    if f.get("vendedor"):
        conds.append(_vis.sql_vendedor("e") + " = %s"); vals.append(f["vendedor"])
    return ((" and " + " and ".join(conds)) if conds else ""), vals


def _faixa_hora(dt: datetime) -> str:
    b = dt.astimezone(_TZ)
    if b.weekday() >= 5:
        return "fds"
    return "comercial" if COMERCIAL[0] <= b.hour < COMERCIAL[1] else "noite"


# ---------------------------------------------------------------- o placar

def _fim_do_dia(fim: datetime) -> datetime:
    """O fim do último dia do período, em Teresina. O período "mês" do Raio-X
    termina AGORA, e a visita das 17h de hoje ficava fora dele às 10h — enquanto o
    Relatório, que conta o dia inteiro, já a listava. Só a visita usa isto."""
    d = (fim - timedelta(microseconds=1)).astimezone(_TZ).date() + timedelta(days=1)
    return datetime.combine(d, datetime.min.time(), tzinfo=_TZ)


def _placar(c, conta_id: int, w: str, wv: list, ini, fim, agora, wc: str = "", wcv: list | None = None,
            wvis: str = "", wvisv: list | None = None, festa: bool = False) -> dict:
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
          from contratos c left join orcamentos o on o.id = c.orcamento_id
         where c.conta_id = %s and {SQL_CT_VIVO} and c.assinado_em >= %s and c.assinado_em < %s{wc}""",
        [conta_id, ini, fim, *(wcv or [])]).fetchone()
    sem_assinar = c.execute(f"""
        select count(*) from orcamentos o join prospeccao p on p.orcamento_id = o.id
         where p.conta_id = %s and o.aprovada_em is not null{w}
           and not exists (select 1 from contratos c where c.orcamento_id = o.id and c.status = 'assinado')""",
        [conta_id, *wv]).fetchone()[0]
    # PARADO EM CASA: quanto o contrato espera AQUI DENTRO antes de ir pro cliente.
    # Medido na conta 34 em 14/09/2026, nos 6 contratos assinados: 35 dias somados
    # parados em casa contra 1 dia esperando o cliente — todos assinaram no mesmo
    # dia em que receberam. A espera que o placar não mostrava era a nossa.
    #
    # Duas contas diferentes e as duas importam: a MEDIANA do que já saiu (o hábito)
    # e quantos estão parados AGORA (o que ainda dá pra resolver hoje). `enviado_em`
    # e não `status`, que nasce 'enviado' por padrão e mente.
    parado = c.execute(f"""
        select
          percentile_cont(0.5) within group (
            order by extract(day from (c.enviado_em - c.criado_em))
          ) filter (where c.enviado_em is not null
                      and c.enviado_em >= %s and c.enviado_em < %s),
          count(*) filter (where c.enviado_em is null and c.status <> 'cancelado')
          from contratos c join orcamentos o on o.id = c.orcamento_id
          join prospeccao p on p.orcamento_id = o.id
         where c.conta_id = %s{w}""", [ini, fim, conta_id, *wv]).fetchone()
    # A VISITA pela régua de `finance.visita` (24/09/2026) — a mesma do Relatório →
    # Funil: sem card conta, é de quem é o card (sem card, de quem marcou), festa
    # não é visita. O desfecho só se pergunta de quem já passou; as que ainda vão
    # acontecer no período entram à parte, pra soma bater com a lista do Relatório.
    vis = c.execute(f"""
        select count(*) filter (where e.inicio < now() and e.desfecho = 'realizado'),
               count(*) filter (where e.inicio < now() and e.desfecho = 'nao_realizado'),
               count(*) filter (where e.inicio < now() and e.desfecho is null),
               count(*) filter (where e.inicio >= now())
          from eventos_agenda e
         where e.conta_id = %s and {_vis.sql_conta("e", festa=festa)}
           and e.inicio >= %s and e.inicio < %s{wvis}""",
        [conta_id, ini, _fim_do_dia(fim), *(wvisv or [])]).fetchone()
    vis_ok, vis_nao, vis_sem, vis_fut = (int(x) for x in vis)
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
        "parado_em_casa": (round(float(parado[0])) if parado and parado[0] is not None else None),
        "parado_em_casa_agora": int((parado[1] if parado else 0) or 0),
        "visitas_ok": vis_ok, "visitas_nao": vis_nao, "visitas_sem_resposta": vis_sem,
        "visitas_futuras": vis_fut,
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
    """Por tipo de festa: quantos LEADS entraram e quanto se PROPÔS no período.

    SÃO DUAS PERGUNTAS E DUAS DATAS, e é por isso que são duas consultas. A conta
    só existe separada depois de 16/09/2026, quando o dono olhou a tela e disse
    "acho que está errado" — e estava.

    O DEFEITO: o período filtrava `prospeccao.criado_em` pros dois números. Então
    uma proposta FEITA em setembro pra um lead que entrou em agosto não existia
    aqui. Na Prime, em setembro, isso escondia as duas propostas de Aniversário —
    R$ 5.000 e R$ 8.600, esta última FECHADA — e a tela dizia "sem proposta" no
    tipo. Também estreitava o Casamento: R$ 7.900 era a média de duas das três
    propostas do mês (a de R$ 6.500 ficou de fora porque o lead era de agosto).

    A PROVA de que era defeito e não critério estava na mesma tela: o bloco de
    propostas enviadas, logo acima, filtra por `orcamentos.criado_em`. Os dois
    falavam das mesmas propostas e contavam períodos diferentes — o de cima dizia
    seis, o de baixo mostrava três.

    Então agora: `n` conta lead que ENTROU no período (é a procura por tipo), e
    `n_orc`/`ticket_centavos` contam proposta FEITA no período (é o preço). A tela
    mostra os dois lado a lado justamente pra nenhum ser lido como o outro.
    """
    leads = c.execute(f"""
        select p.evento_tipo, count(*)
          from prospeccao p
         where p.conta_id = %s and p.criado_em >= %s and p.criado_em < %s{w}
         group by 1""", [conta_id, ini, fim, *wv]).fetchall()
    # o `join` (e não `left join`) é o que diz "proposta feita no período": sem
    # proposta não há data de proposta, e a linha não tem por que existir aqui.
    # `coalesce(primeiro_ano, setup)` é como o resto da casa lê o total de um
    # orçamento (ver `agenda`, `cockpit`, `vendas`) — só aqui era o primeiro sem
    # a rede, e um orçamento de valor único cairia como "sem valor".
    props = c.execute(f"""
        select p.evento_tipo,
               count(*) filter (where o.status <> 'rascunho'),
               avg(coalesce(o.primeiro_ano_centavos, o.setup_centavos))
                 filter (where o.status <> 'rascunho'
                           and coalesce(o.primeiro_ano_centavos, o.setup_centavos, 0) > 0),
               count(*) filter (where o.status <> 'rascunho'
                                  and coalesce(o.primeiro_ano_centavos, o.setup_centavos, 0) > 0)
          from orcamentos o join prospeccao p on p.orcamento_id = o.id
         where p.conta_id = %s and o.criado_em >= %s and o.criado_em < %s{w}
         group by 1""", [conta_id, ini, fim, *wv]).fetchall()

    agg: dict[str, dict] = {}

    def _slot(t):
        k = _tipo_canonico(t)
        return agg.setdefault(k, {"tipo": k, "n": 0, "soma": 0.0, "n_orc": 0, "n_valor": 0})

    for t, n in leads:
        _slot(t)["n"] += int(n)
    for t, n_orc, media, n_valor in props:
        a = _slot(t)
        a["n_orc"] += int(n_orc or 0)
        if media is not None:
            # O PESO é quantas propostas TÊM valor, não quantas existem: a média
            # que vem do banco já ignorou as de valor zero, e multiplicá-la pelo
            # total inflava o tipo que tivesse uma proposta sem valor. Só aparece
            # quando dois `evento_tipo` crus caem no mesmo tipo canônico ("Chá" e
            # "Confraternização" viram "Outro"), que é onde ninguém ia procurar.
            a["soma"] += float(media) * int(n_valor); a["n_valor"] += int(n_valor)
    out = [{"tipo": a["tipo"], "n": a["n"], "n_orc": a["n_orc"],
            "ticket_centavos": int(a["soma"] / a["n_valor"]) if a["n_valor"] else None}
           for a in agg.values()]
    out.sort(key=lambda x: (x["tipo"] == "sem tipo", -(x["ticket_centavos"] or 0), -x["n"]))
    return out


def _ciclo(c, conta_id, w, wv, ini, fim, wc: str = "", wcv: list | None = None) -> dict:
    lp = c.execute(f"""
        select coalesce(m.nome, '—'), extract(epoch from o.criado_em - p.criado_em) / 86400
          from orcamentos o join prospeccao p on p.orcamento_id = o.id
          left join membros m on m.id = p.vendedor_id
         where p.conta_id = %s and o.status <> 'rascunho' and o.criado_em >= %s and o.criado_em < %s{w}""",
        [conta_id, ini, fim, *wv]).fetchall()
    pc = c.execute(f"""
        select extract(epoch from c.assinado_em - o.criado_em) / 86400
          from contratos c join orcamentos o on o.id = c.orcamento_id
         where c.conta_id = %s and {SQL_CT_VIVO} and c.assinado_em >= %s and c.assinado_em < %s{wc}""",
        [conta_id, ini, fim, *(wcv or [])]).fetchall()
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
         where p.conta_id = %s and p.status = 'perdido'
           -- a DATA DA PERDA (235); a última edição só na falta dela — editar um
           -- perdido antigo não pode trazê-lo pro mês de hoje (cockpit_dono._PERDEU_EM).
           -- `to_jsonb` lê a coluna sem exigi-la, como em finance.visita
           and coalesce((to_jsonb(p) ->> 'perda_em')::timestamptz, p.atualizado_em) >= %s
           and coalesce((to_jsonb(p) ->> 'perda_em')::timestamptz, p.atualizado_em) < %s{w}
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


# ---------------------------------------------------------------- da visita ao contrato

def _visita_ok(festa: bool) -> str:
    """A VISITA QUE ACONTECEU: a régua do placar (`finance.visita`) com desfecho
    "realizado". Era `tipo = 'empresa'` e só com card — o placar e este bloco
    passaram a contar a mesma coisa que o Relatório → Funil em 24/09/2026."""
    return _vis.sql_conta("e", festa=festa) + " and e.desfecho = 'realizado'"

#: O vendedor do orçamento: quem o fez; na falta, o vendedor do lead. É a régua
#: de SQL_CT_VENDEDOR, só que pra quem ainda não tem contrato.
_ORC_VENDEDOR = """coalesce(
        case when o.criado_por ~ '^[0-9]+$' then o.criado_por::bigint end,
        (select p.vendedor_id from prospeccao p
          where p.orcamento_id = o.id and p.conta_id = o.conta_id order by p.id limit 1))"""


def _where_orcamento(f: dict) -> tuple[str, list]:
    """Os filtros sobre `orcamentos o`, como `_where_contrato` faz com o contrato:
    vendedor é quem fez o orçamento, e os filtros do lead viram "tem um lead assim"."""
    conds, vals = [], []
    w_lead, wv_lead = _where({**f, "vendedor": None})
    if w_lead:
        conds.append("exists (select 1 from prospeccao p where p.orcamento_id = o.id "
                     "and p.conta_id = o.conta_id" + w_lead + ")")
        vals += wv_lead
    if f.get("vendedor"):
        conds.append(_ORC_VENDEDOR + " = %s"); vals.append(f["vendedor"])
    return ((" and " + " and ".join(conds)) if conds else ""), vals


def _dia(dt):
    return dt.astimezone(_TZ).date() if dt else None


def da_visita(c, conta_id: int, f: dict, ini, fim, festa: bool = False) -> dict:
    """DA VISITA AO CONTRATO (pedido do dono em 24/09/2026, mockup
    docs/mockups/prime_visita_ao_contrato.html): quatro degraus do MESMO período,
    cada um pela sua data — a visita pelo dia em que aconteceu, o orçamento de quem
    visitou, a proposta pelo dia em que o cliente aceitou (`aprovada_em`) e o
    contrato pelo dia da assinatura — e os clientes de cada ponta.

    A conversão da visita (resposta do dono): a visita REALIZADA no período, e se
    aquele lead tem orçamento, feito antes ou depois dela."""
    w, wv = _where(f)
    wc, wcv = _where_contrato(f)
    wo, wov = _where_orcamento(f)
    wvis, wvisv = _where_visita(f)
    # QUEM FOI MARCADO: uma pessoa por card; a visita sem card é uma pessoa sozinha
    # (não há card pra juntar duas visitas da mesma cliente).
    marcadas = c.execute(f"""
        select count(distinct coalesce('l' || e.prospeccao_id::text, 'e' || e.id::text))
          from eventos_agenda e
         where e.conta_id = %s and {_vis.sql_conta("e", festa=festa)}
           and e.inicio >= %s and e.inicio < least(%s, now()){wvis}""",
        [conta_id, ini, fim, *wvisv]).fetchone()[0]
    vis = c.execute(f"""
        select p.id, coalesce(nullif(p.empresa, ''), nullif(p.contato, ''), 'Lead'), p.vendedor_id,
               p.orcamento_id, coalesce(o.primeiro_ano_centavos, o.setup_centavos, 0),
               exists (select 1 from contratos c where c.orcamento_id = p.orcamento_id
                          and c.conta_id = p.conta_id and {SQL_CT_VIVO})
          from prospeccao p left join orcamentos o on o.id = p.orcamento_id
         where p.conta_id = %s and exists (
               select 1 from eventos_agenda e where e.prospeccao_id = p.id and e.conta_id = p.conta_id
                  and {_visita_ok(festa)} and e.inicio >= %s and e.inicio < %s){w}
         order by p.id""", [conta_id, ini, fim, *wv]).fetchall()
    # A VISITA SEM CARD (24/09/2026) também aconteceu, e conta — mas não tem de onde
    # tirar orçamento nem contrato, então vai pra uma lista própria em vez de cair
    # em "sem orçamento", que diria de quem fechou contrato que ainda não orçou (a
    # Renata da Prime visitou sem card e assinou três dias depois). Com filtro de
    # LEAD (tipo de festa, origem...) ela sai, como em `_where_visita`.
    sem_card = []
    if not _where({**f, "vendedor": None})[0]:
        q = f"""select e.id, e.titulo, e.membro_id
                  from eventos_agenda e
                 where e.conta_id = %s and e.prospeccao_id is null and {_visita_ok(festa)}
                   and e.inicio >= %s and e.inicio < %s"""
        args = [conta_id, ini, fim]
        if f.get("vendedor"):
            q += " and e.membro_id = %s"
            args.append(f["vendedor"])
        crus = c.execute(q + " order by e.inicio", args).fetchall()
        # o NOME sai do título pela régua da Agenda, que conhece a equipe: a equipe
        # batiza "VISITA TÉCNICA - PEDRO", e sem a lista o cliente viraria o vendedor
        from finance import agenda as _ag
        equipe = [r[0] for r in c.execute("select nome from membros where conta_id = %s and nome is not null",
                                          (conta_id,)).fetchall()] if crus else []
        sem_card = [(eid, _ag.nome_no_titulo(t, None, equipe) or (t or "Visita"), mid)
                    for eid, t, mid in crus]
    prop = c.execute(f"""
        select o.id, {_ORC_VENDEDOR}, coalesce(o.primeiro_ano_centavos, o.setup_centavos, 0),
               exists (select 1 from contratos c where c.orcamento_id = o.id
                          and c.conta_id = o.conta_id and {SQL_CT_VIVO})
          from orcamentos o
         where o.conta_id = %s and o.aprovada_em >= %s and o.aprovada_em < %s{wo}""",
        [conta_id, ini, fim, *wov]).fetchall()
    cts = c.execute(f"""
        select c.numero, coalesce(nullif(o.empresa, ''), nullif(o.cliente, ''), 'Contrato'),
               o.aprovada_em, c.assinado_em, coalesce(c.valor_centavos, 0), {SQL_CT_VENDEDOR}
          from contratos c left join orcamentos o on o.id = c.orcamento_id
         where c.conta_id = %s and {SQL_CT_VIVO}
           and c.assinado_em >= %s and c.assinado_em < %s{wc}
         order by c.assinado_em""", [conta_id, ini, fim, *wcv]).fetchall()

    linhas = []
    for num, nome, prop_em, ct_em, valor, _mid in cts:
        dp, dc = _dia(prop_em), _dia(ct_em)
        linhas.append({"numero": num, "nome": nome, "proposta_em": dp, "contrato_em": dc,
                       "espera": (max(0, (dc - dp).days) if dp and dc else None),
                       "valor_centavos": int(valor or 0),
                       # a proposta foi aceita ANTES do período: é o contrato que
                       # "sobra" quando os contratos passam das propostas do mês
                       "proposta_antes": bool(dp and dp < ini.astimezone(_TZ).date())})
    esperas = [x["espera"] for x in linhas if x["espera"] is not None]

    com_orc = [v for v in vis if v[3]]
    # por vendedor: a MESMA régua de cada degrau (visita pelo lead dele; proposta e
    # contrato por quem fez o orçamento)
    por: dict = {}

    def _v(mid):
        return por.setdefault(mid, {"visitas": 0, "vis_orc": 0, "prop_ass": 0, "prop_ass_valor": 0,
                                    "contratos": 0, "contratos_valor": 0})
    for v in vis:
        _v(v[2])["visitas"] += 1
        if v[3]:
            _v(v[2])["vis_orc"] += 1
    for _eid, _nome, mid in sem_card:
        _v(mid)["visitas"] += 1
    for _oid, mid, valor, _ct in prop:
        _v(mid)["prop_ass"] += 1
        _v(mid)["prop_ass_valor"] += int(valor or 0)
    for *_x, valor, mid in cts:
        _v(mid)["contratos"] += 1
        _v(mid)["contratos_valor"] += int(valor or 0)

    return {
        "marcadas": int(marcadas or 0),
        "visitas": len(vis) + len(sem_card),
        "vis_orc": len(com_orc),
        "vis_orc_valor": sum(int(v[4] or 0) for v in com_orc),
        # a taxa é sobre a visita COM card: a sem card não tem de onde tirar
        # orçamento, e contá-la como "não virou" diria 0% da Renata, que assinou
        "vis_orc_pct": (round(100 * len(com_orc) / len(vis)) if vis else None),
        "prop_ass": len(prop),
        "prop_ass_valor": sum(int(x[2] or 0) for x in prop),
        "prop_ass_com_contrato": sum(1 for x in prop if x[3]),
        "contratos": len(cts),
        "contratos_valor": sum(int(x[4] or 0) for x in cts),
        "contratos_de_antes": sum(1 for x in linhas if x["proposta_antes"]),
        # os clientes de cada ponta
        "sem_orcamento": [v[1] for v in vis if not v[3]],
        "sem_card": [x[1] for x in sem_card],
        "em_jogo": [{"nome": v[1], "valor_centavos": int(v[4] or 0)} for v in com_orc if not v[5]],
        "em_jogo_valor": sum(int(v[4] or 0) for v in com_orc if not v[5]),
        "assinaram": [v[1] for v in com_orc if v[5]],
        "linhas": linhas,
        "espera_mediana": _mediana(esperas),
        "por_vendedor": por,
    }


# ---------------------------------------------------------------- os blocos do recorrente

def _proposto_x_fechado(c, conta_id, w, wv, ini, fim, coluna: str,
                        wc: str = "", wcv: list | None = None) -> list[dict]:
    """Proposto × fechado por mês, somando UMA coluna de valor do orçamento.

    A coluna é escolhida pelo PERFIL e nunca vem de fora: `mensal_centavos` pra
    quem fatura mensalidade, `setup_centavos` pra quem fatura por venda — numa
    corretora a comissão da apólice é valor único, e somar a mensalidade dava R$ 0
    todo mês (medido em 13/09/2026, antes do perfil `seguros` existir).
    """
    assert coluna in ("mensal_centavos", "setup_centavos")  # nunca entrada de usuário
    prop = dict(c.execute(f"""
        select to_char(o.criado_em at time zone 'America/Sao_Paulo', 'YYYY-MM'), coalesce(sum(o.{coluna}), 0)
          from orcamentos o join prospeccao p on p.orcamento_id = o.id
         where p.conta_id = %s and o.status <> 'rascunho' and o.criado_em >= %s and o.criado_em < %s{w}
         group by 1""", [conta_id, ini, fim, *wv]).fetchall())
    fech = dict(c.execute(f"""
        select to_char(c.assinado_em at time zone 'America/Sao_Paulo', 'YYYY-MM'), coalesce(sum(o.{coluna}), 0)
          from contratos c join orcamentos o on o.id = c.orcamento_id
         where c.conta_id = %s and {SQL_CT_VIVO} and c.assinado_em >= %s and c.assinado_em < %s{wc}
         group by 1""", [conta_id, ini, fim, *(wcv or [])]).fetchall())
    meses = []
    m = ini.date().replace(day=1)
    while m < fim.date():
        k = m.strftime("%Y-%m")
        meses.append({"mes": k, "rotulo": _MESES[m.month - 1], "proposta": int(prop.get(k, 0)), "fechada": int(fech.get(k, 0))})
        m = (m.replace(day=28) + timedelta(days=4)).replace(day=1)
    return meses


def _mrr(c, conta_id, w, wv, ini, fim, wc: str = "", wcv: list | None = None) -> list[dict]:
    """Mensalidade proposta × fechada, por mês do período (MRR novo)."""
    return _proposto_x_fechado(c, conta_id, w, wv, ini, fim, "mensal_centavos", wc, wcv)


def _comissao(c, conta_id, w, wv, ini, fim, wc: str = "", wcv: list | None = None) -> list[dict]:
    """O mesmo recorte pra corretora: valor único proposto × fechado, por mês.

    Um contrato assinado aqui é uma apólice emitida, e o que interessa ao dono é
    quanto ela vale — o `setup_centavos` do orçamento. A tela rotula sem "/mês",
    que é a diferença visível pra quem vinha vendo o bloco de mensalidade.
    """
    return _proposto_x_fechado(c, conta_id, w, wv, ini, fim, "setup_centavos", wc, wcv)


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
    wc, wcv = _where_contrato(f)
    wvis, wvisv = _where_visita(f)
    # quem vende festa conta visita só pelo título (ver finance/visita.py)
    festa = bool((perfil.get("vocab") or {}).get("data"))
    blocos = set(perfil.get("blocos") or ())
    out = {"ini": ini, "fim": fim, "rotulo": rot, "filtros": f, "perfil": perfil, "placar": None, "anterior": None,
           "demanda_agenda": None, "dia_festa": None, "tipos": None, "ciclo": None, "perdas": None,
           "mrr": None, "comissao": None, "segmentos": None, "servicos": None, "da_visita": None,
           "vendedores": [], "confianca": None}
    todos = (("placar", lambda: _placar(c, conta_id, w, wv, ini, fim, a, wc, wcv, wvis, wvisv, festa)),
             ("anterior", lambda: _placar(c, conta_id, w, wv, ant_ini, ant_fim, a, wc, wcv, wvis, wvisv, festa)),
             ("demanda_agenda", lambda: _demanda_agenda(c, conta_id, w, wv, a.date())),
             ("dia_festa", lambda: _dia_festa(c, conta_id, w, wv, ini, fim)),
             ("tipos", lambda: _tipos_ticket(c, conta_id, w, wv, ini, fim)),
             ("mrr", lambda: _mrr(c, conta_id, w, wv, ini, fim, wc, wcv)),
             ("comissao", lambda: _comissao(c, conta_id, w, wv, ini, fim, wc, wcv)),
             ("segmentos", lambda: _segmentos(c, conta_id, w, wv, ini, fim)),
             ("servicos", lambda: _servicos(c, conta_id, w, wv, ini, fim)),
             ("ciclo", lambda: _ciclo(c, conta_id, w, wv, ini, fim, wc, wcv)),
             ("perdas", lambda: _perdas(c, conta_id, w, wv, ini, fim, perfil.get("motivos") or MOTIVOS_TODOS)),
             ("da_visita", lambda: da_visita(c, conta_id, f, ini, fim, festa)))
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
