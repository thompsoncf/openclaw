"""A VISTA ATENDIMENTO DO FUNIL (27/09/2026, docs/mockups/funil_atendimento.html,
aprovada pelo dono "com as recomendações").

O dono: "no funil não consigo ver o que atende vendedor e o IA — não seria legal
colocar o atendimento num funil pra gente até medir depois as etapas?". O funil de
sempre é o de VENDAS: etapas que a equipe move à mão. Esta é a outra vista: o COMEÇO
da conversa, em etapas que ninguém arrasta — o sistema lê a conversa, a ficha e a
agenda — e o topo compara a IA com a equipe etapa por etapa.

As etapas (a mais adiantada que o lead alcançou):

  chegou       o lead do período que escreveu pelo WhatsApp; ninguém respondeu ainda
  respondido   a 1ª resposta da empresa, da IA ou de gente (o recado de fora do
               horário, o envio que falhou e a SAUDAÇÃO AUTOMÁTICA do celular não
               contam — finance/saudacao.py); é o mesmo relógio do Desafio
  qualificado  os campos que o leitor de conversa preenche: festa, data e convidados
               (eventos); segmento e porte (recorrente)
  ofertada     a empresa convidou pra visita/reunião (a IA com os horários, ou gente
               falando nisso)
  marcada      a visita/reunião está na agenda, pela régua de `finance.visita`
  parou        perdido, ou 3 dias sem mensagem do cliente antes de marcar — com a
               etapa em que parou

§6: só pra quem tem funil de atendimento — `eventos` e `recorrente`. Os outros
perfis (seguros, clínica, obras, produto) não ganham a vista até o dono dizer o que
é "qualificado" pra eles. Quem atende: a IA (o membro da regra por número com a IA
ligada, ou o do resgate — `chip_regra.membros_ia`) ou a equipe (o dono do card).
O lead do número do supervisor do resgate fica de fora, como no Desafio.
"""
from __future__ import annotations

import logging
import statistics
from datetime import datetime, timedelta, timezone

from finance import visita as vis

_log = logging.getLogger(__name__)

_TZ = "America/Sao_Paulo"
_BRT = timezone(timedelta(hours=-3))

PERFIS = ("eventos", "recorrente")
ETAPAS = ("chegou", "respondido", "qualificado", "ofertada", "marcada")
PAROU_DIAS = 3
PERIODOS = (("mes", "este mês"), ("7d", "7 dias"))

# o convite pra o próximo passo, na fala da empresa
_OFERTA = {"eventos": r"(visita|conhecer o espa[cç]o|conhe[cç]a o espa[cç]o)",
           "recorrente": r"(reuni[aã]o|videochamada|uma call|conversar por v[ií]deo)"}


def rotulos(perfil: str) -> dict:
    """O nome e a linha de explicação de cada coluna, pelo nicho (§6)."""
    festa = perfil == "eventos"
    comp = "visita" if festa else "reunião"
    return {
        "chegou": ("Chegou", "ninguém respondeu ainda"),
        "respondido": ("Respondido", "falta " + ("festa, data e convidados" if festa else "segmento e porte")),
        "qualificado": ("Qualificado", "festa, data e convidados" if festa else "segmento e porte"),
        "ofertada": (f"{comp.capitalize()} ofertada", "convidado a conhecer o espaço" if festa
                     else "convidado pra uma reunião"),
        "marcada": (f"{comp.capitalize()} marcada", "está na agenda"),
        "parou": ("Parou", "sumiu, não quer, ou foi perdido"),
    }


def janela(periodo: str, agora: datetime) -> tuple[datetime, datetime, str]:
    a = agora.astimezone(_BRT)
    if periodo == "7d":
        ini = (a - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
        return ini, a + timedelta(seconds=1), "últimos 7 dias"
    ini = a.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    meses = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto",
             "setembro", "outubro", "novembro", "dezembro")
    return ini, a + timedelta(seconds=1), meses[a.month - 1]


def _tem(c, tabela: str) -> bool:
    try:
        with c.transaction():
            c.execute(f"select 1 from {tabela} limit 1")
        return True
    except Exception:  # noqa: BLE001
        return False


def _linhas(pool, conta_id: int, perfil: str, ini: datetime, fim: datetime) -> list[dict]:
    from finance import resgate as _rg
    from finance import saudacao as _sd
    festa = perfil == "eventos"
    with pool.connection() as c:
        regra_sql = ("(select r.membro_id from chip_regra_leads r where r.prospeccao_id = p.id "
                     "and r.conta_id = p.conta_id)" if _tem(c, "chip_regra_leads") else "null::bigint")
        fora, fora_v = _rg.sql_fora_do_supervisor(c, conta_id)
        agenda = _tem(c, "eventos_agenda")
        c.commit()
    qual = ("(coalesce(p.evento_tipo,'') <> '' and p.evento_em is not null "
            "and p.evento_convidados is not null)" if festa
            else "(coalesce(p.segmento,'') <> '' and coalesce(p.porte,'') <> '')")
    marcada = (f"""exists (select 1 from eventos_agenda e where e.conta_id = %s
                             and e.prospeccao_id = l.id and {vis.sql_conta('e', festa=festa)})"""
               if agenda else "false")
    sql = f"""
        with l as (
          select p.id, p.vendedor_id, {regra_sql} regra_membro, p.status,
                 coalesce(nullif(p.contato,''), nullif(p.empresa,''), '') nome, {qual} qualif
            from prospeccao p
           where p.conta_id = %s and p.criado_em >= %s and p.criado_em < %s{fora}),
        cv as (
          select cv.id, cv.prospeccao_id lead, cv.chip_id
            from conversas cv join l on l.id = cv.prospeccao_id
           where cv.conta_id = %s and cv.canal = 'whatsapp'),
        pin as (
          select cv.lead, min(m.criado_em) t, max(m.criado_em) ult
            from cv join mensagens m on m.conversa_id = cv.id and m.direcao = 'in'
           group by cv.lead),
        pout as (
          select pin.lead, min(m.criado_em) t,
                 bool_or(m.texto ~* %s) ofertou
            from pin join cv on cv.lead = pin.lead
            join conversas cvx on cvx.id = cv.id
            join mensagens m on m.conversa_id = cv.id and m.direcao = 'out'
                            and m.criado_em >= pin.t
                            and coalesce(m.status, '') not in ('ia_fora', 'erro')
                            and {_sd.sql_nao_saudacao('m', 'cvx')}
           group by pin.lead),
        chip as (
          select distinct on (cv.lead) cv.lead, cv.chip_id from cv order by cv.lead, cv.id)
        select l.id, l.vendedor_id, l.regra_membro, l.status, l.nome, l.qualif,
               pin.t, pin.ult, pout.t, coalesce(pout.ofertou, false), {marcada}, chip.chip_id
          from l join pin on pin.lead = l.id
          left join pout on pout.lead = l.id
          left join chip on chip.lead = l.id"""
    vals = [conta_id, ini, fim, *fora_v, conta_id, _OFERTA[perfil]]
    if agenda:
        vals.append(conta_id)
    with pool.connection() as c:
        rows = c.execute(sql, vals).fetchall()
    return [dict(zip(("id", "vendedor", "regra_membro", "status", "nome", "qualif", "t_in",
                      "ult_in", "t_resp", "ofertou", "marcada", "chip"), r)) for r in rows]


def _etapa(x: dict) -> str:
    if x["marcada"]:
        return "marcada"
    if x["ofertou"]:
        return "ofertada"
    if x["t_resp"] and x["qualif"]:
        return "qualificado"
    if x["t_resp"]:
        return "respondido"
    return "chegou"


def _parou(x: dict, agora: datetime) -> bool:
    if (x["status"] or "") == "perdido":
        return True
    return (not x["marcada"] and x["ult_in"] is not None
            and x["ult_in"] < agora - timedelta(days=PAROU_DIAS))


def _mediana_min(xs: list[dict]) -> float | None:
    ts = [(x["t_resp"] - x["t_in"]).total_seconds() / 60 for x in xs if x["t_resp"] and x["t_in"]]
    return round(statistics.median(ts), 1) if ts else None


def _regua(xs: list[dict]) -> dict:
    """Das que chegaram, quantas passaram por cada etapa (parado conta até onde foi)."""
    n = len(xs)
    ordem = {e: i for i, e in enumerate(ETAPAS)}
    out = {"chegou": n, "resp_min": _mediana_min(xs)}
    for e in ETAPAS[1:]:
        k = sum(1 for x in xs if ordem[x["etapa"]] >= ordem[e])
        out[e] = k
        out[e + "_pct"] = round(100 * k / n) if n else None
    return out


def _dur(seg: float) -> str:
    seg = max(0, int(seg))
    if seg < 90:
        return f"{seg} s"
    m = seg // 60
    if m < 90:
        return f"{m} min"
    h = m // 60
    return f"{h} h" if h < 48 else f"{h // 24} dias"


def dados(pool, conta_id: int, perfil: str, *, periodo: str = "mes", quem: str = "",
          chip: str = "", agora: datetime | None = None) -> dict | None:
    """Tudo o que a vista mostra. None quando o nicho não tem a vista (§6)."""
    if perfil not in PERFIS:
        return None
    agora = agora or datetime.now(timezone.utc)
    periodo = periodo if periodo in dict(PERIODOS) else "mes"
    ini, fim, rot = janela(periodo, agora)
    from finance import chip_regra as _cr
    from finance import resgate as _rg
    with pool.connection() as c:
        ia_ids = _cr.membros_ia(c, conta_id)
        nomes = {int(r[0]): (r[1] or "").strip() for r in c.execute(
            "select id, coalesce(nullif(nome,''), email) from membros where conta_id=%s",
            (conta_id,)).fetchall()}
        c.commit()
    todos = _linhas(pool, conta_id, perfil, ini, fim)
    chips = {}
    with pool.connection() as c:
        for x in todos:
            k = x["chip"] or conta_id
            if k not in chips:
                chips[k] = _rg.nome_do_chip(c, conta_id, k)
        c.commit()
    for x in todos:
        mid = x["regra_membro"] if x["regra_membro"] in ia_ids else (
            x["vendedor"] if x["vendedor"] in ia_ids else None)
        x["ia"] = mid is not None
        x["quem"] = _primeiro(nomes.get(mid or x["vendedor"] or 0, "")) or "sem dono"
        x["etapa"] = _etapa(x)
        x["parou"] = _parou(x, agora)
        x["chip_id"] = x["chip"] or conta_id
        x["chip_nome"] = chips.get(x["chip_id"], "")
        x["nome"] = _primeiro(x["nome"]) or f"#{x['id']}"
        if x["t_resp"]:
            x["tempo"] = "respondida em " + _dur((x["t_resp"] - x["t_in"]).total_seconds())
            x["lento"] = (x["t_resp"] - x["t_in"]) > timedelta(minutes=15)
        else:
            x["tempo"] = "esperando " + _dur((agora - x["t_in"]).total_seconds())
            x["lento"] = (agora - x["t_in"]) > timedelta(minutes=15)
    # a régua compara os lados SEMPRE com tudo (o filtro de quem atende é do quadro)
    no_chip = [x for x in todos if not chip or str(x["chip_id"]) == str(chip)]
    ia = [x for x in no_chip if x["ia"]]
    eq = [x for x in no_chip if not x["ia"]]
    regua = []
    if ia:
        mid_ia = next(x["regra_membro"] if x["regra_membro"] in ia_ids else x["vendedor"] for x in ia)
        regua.append(dict(_regua(ia), quem=_primeiro(nomes.get(mid_ia, "")) or "IA", ia=True))
    regua.append(dict(_regua(eq), quem="equipe", ia=False))
    mostra = [x for x in no_chip if (quem != "ia" or x["ia"]) and (quem != "equipe" or not x["ia"])]
    colunas = []
    rot_cols = rotulos(perfil)
    for e in ETAPAS + ("parou",):
        if e == "parou":
            cards = [x for x in mostra if x["parou"]]
        else:
            cards = [x for x in mostra if not x["parou"] and x["etapa"] == e]
        cards.sort(key=lambda x: x["t_in"], reverse=True)
        colunas.append({"chave": e, "titulo": rot_cols[e][0], "sub": rot_cols[e][1],
                        "n": len(cards), "cards": cards})
    return {"periodo": periodo, "periodo_rot": rot, "periodos": PERIODOS, "quem": quem,
            "chip": str(chip or ""), "chips": sorted(chips.items(), key=lambda kv: kv[0] != conta_id),
            "total": len(no_chip), "regua": regua, "colunas": colunas, "tem_ia": bool(ia_ids),
            "perfil": perfil, "rot_parou": rot_cols}


def _primeiro(nome: str) -> str:
    p = str(nome or "").strip().split()
    return p[0].title() if p else ""
