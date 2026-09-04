"""O CARD LÊ A CONVERSA: data, tipo e convidados do evento tirados do que o cliente
escreveu — por regra, sem IA, sempre igual.

POR QUE. Prime Eventos (conta 34), 04/09/2026: o cliente escreve "seria para
casamento, data 13 de fevereiro" e "média de 70 pessoas", e o card ao lado diz
"sem data · perguntar". Dos 246 leads sem data, 121 já tinham dito a data na
conversa, 83 os convidados, 121 o tipo. O agente lê isso quando é ele quem
responde (finance/agente.py); onde quem responde é gente, nada lia.

O QUE FAZ. `ler_texto` tira de UMA mensagem o que dá pra tirar; `ler_mensagens`
junta as mensagens do cliente (a fala mais recente de cada coisa vale);
`ler_conversa` aplica no lead: preenche SÓ o que está vazio (evento_lead.gravar,
so_vazios), marca a origem "conversa" com o trecho de prova (migração 198), e
guarda como PISTA o que ouviu mas não gravou — "falou de março" quando não há
dia, "falou de 20 fev" quando a conversa diz uma data diferente da que está no
lead. Decisão do dono, 04/09: o que o leitor achou já vale, com o selo no card;
mudar o que já está lá é do vendedor.

QUANDO RODA. Em toda mensagem de entrada (os três webhooks chamam `ler_conversa_bg`
em segundo plano, com ou sem agente) e pelo botão "Ler as conversas" do funil,
que passa pelo acervo de quem já chegou.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone

from finance import evento_lead as _evl

_log = logging.getLogger("evento_leitor")

_MESES = {
    "janeiro": 1, "jan": 1, "fevereiro": 2, "fev": 2, "março": 3, "marco": 3, "mar": 3,
    "abril": 4, "abr": 4, "maio": 5, "mai": 5, "junho": 6, "jun": 6, "julho": 7, "jul": 7,
    "agosto": 8, "ago": 8, "setembro": 9, "set": 9, "outubro": 10, "out": 10,
    "novembro": 11, "nov": 11, "dezembro": 12, "dez": 12,
}
_MESES_CHEIOS = ("janeiro", "fevereiro", "março", "marco", "abril", "maio", "junho", "julho",
                 "agosto", "setembro", "outubro", "novembro", "dezembro")
_NOME_MES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto",
             "setembro", "outubro", "novembro", "dezembro"]

# "13/02", "14/11/2026", "22/05/27" — o ':' de horário não entra ("20:00" não é data)
_RE_NUM = re.compile(r"(?<![\d/])(\d{1,2})\s*/\s*(\d{1,2})(?:\s*/\s*(\d{2}|\d{4}))?(?![\d/])")
# "13 de fevereiro", "31 de outubro", "18 de dezembro 2026", "13 fev", "17 ou 18 de dezembro"
_RE_TXT = re.compile(
    r"(?:(?P<alt>\d{1,2})\s*(?:ou|e|a)\s*)?(?P<dia>\d{1,2})\s*(?:de\s+)?"
    r"(?P<mes>janeiro|fevereiro|março|marco|abril|maio|junho|julho|agosto|setembro|outubro"
    r"|novembro|dezembro|jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)\b"
    r"(?:\s*(?:de\s+)?(?P<ano>\d{4}))?", re.I)
_RE_MES_SOLTO = re.compile(r"\b(" + "|".join(_MESES_CHEIOS) + r")\b", re.I)
_RE_CONV = re.compile(r"(?<![\d/:])(\d{1,4})\s*(?:pessoas?|convidad[oa]s?|pax)\b", re.I)
# "Aniversário de 40 anos": o 40 é idade, não convidado — o _RE_CONV exige pessoas/convidados.
_TIPOS = (
    (r"15\s*anos|quinze\s*anos|debut", "15 anos"),
    (r"casamento|casar\b|noivos|noiva\b", "Casamento"),
    (r"anivers|\bniver\b", "Aniversário"),
    (r"formatura|jaleco|cola[çc][ãa]o", "Formatura"),
    (r"confraterniza", "Confraternização"),
    (r"corporativ|empresarial", "Corporativo"),
    (r"batizado", "Batizado"),
    (r"noivado", "Noivado"),
    (r"bodas", "Bodas"),
    (r"ch[áa]\s+(?:de|revela)", "Chá"),
)
_RE_TIPOS = [(re.compile(rx, re.I), rot) for rx, rot in _TIPOS]

# no máximo 3 anos pra frente: além disso é erro de digitação, não festa
_HORIZONTE_ANOS = 3


def _resolver(dia: int, mes: int, ano: int | None, hoje: date) -> date | None:
    """Monta a data. Sem ano, é o próximo em que ela ainda não passou ("13 de
    fevereiro" dito em setembro de 2026 é 2027). Data passada ou longe demais → None."""
    if ano is not None and ano < 100:
        ano += 2000
    anos = [ano] if ano else [hoje.year, hoje.year + 1]
    for a in anos:
        try:
            d = date(a, mes, dia)
        except ValueError:
            continue
        if hoje <= d <= date(hoje.year + _HORIZONTE_ANOS, 12, 31):
            return d
    return None


def _trecho(texto: str, ini: int, fim: int, folga: int = 45) -> str:
    """O pedaço da mensagem em volta do que foi lido — a prova que vai pro balão."""
    t = " ".join((texto or "").split())
    a, b = max(0, ini - folga), min(len(t), fim + folga)
    s = t[a:b].strip()
    return ("…" if a > 0 else "") + s + ("…" if b < len(t) else "")


def ler_texto(texto: str, hoje: date | None = None) -> dict:
    """O que UMA mensagem do cliente diz. Chaves só quando achou:
    data, data_trecho, alternativa ("17 ou 18 de dezembro"), mes_solto ("março"),
    tipo, convidados, trecho (o pedaço em volta do primeiro achado)."""
    hoje = hoje or date.today()
    t = " ".join((texto or "").split())
    out: dict = {}
    if not t:
        return out
    achados: list[tuple[int, int]] = []
    # a ÚLTIMA data da mensagem vale ("17 ou 18 de dezembro" → 18; "de 10/01 pra 17/01" → 17)
    ultima = None
    for m in _RE_TXT.finditer(t):
        d = _resolver(int(m["dia"]), _MESES[m["mes"].lower()],
                      int(m["ano"]) if m["ano"] else None, hoje)
        if d:
            ultima = (d, m.start(), m.end(), m["alt"])
    for m in _RE_NUM.finditer(t):
        dia, mes = int(m[1]), int(m[2])
        if not (1 <= dia <= 31 and 1 <= mes <= 12):
            continue
        d = _resolver(dia, mes, int(m[3]) if m[3] else None, hoje)
        if d and (ultima is None or m.start() > ultima[1]):
            ultima = (d, m.start(), m.end(), None)
    if ultima:
        d, ini, fim, alt = ultima
        out["data"] = d
        out["data_trecho"] = t[ini:fim]
        if alt:
            out["alternativa"] = t[ini:fim]
        achados.append((ini, fim))
    else:
        m = _RE_MES_SOLTO.search(t)
        if m:
            out["mes_solto"] = _NOME_MES[_MESES[m[1].lower()] - 1]
            achados.append((m.start(), m.end()))
    m = _RE_CONV.search(t)
    if m:
        n = int(m[1])
        if 0 < n < 100000:
            out["convidados"] = n
            achados.append((m.start(), m.end()))
    for rx, rot in _RE_TIPOS:
        m = rx.search(t)
        if m:
            out["tipo"] = rot
            achados.append((m.start(), m.end()))
            break
    if achados:
        ini = min(a for a, _ in achados)
        fim = max(b for _, b in achados)
        out["trecho"] = _trecho(t, ini, fim)
    return out


def ler_mensagens(mensagens, hoje: date | None = None) -> dict:
    """Junta as mensagens do cliente, da mais antiga pra mais nova: a fala mais
    recente de cada coisa vale (o cliente corrige a data, muda os convidados).
    `mensagens` é uma lista de (texto, quando). Devolve data, tipo, convidados,
    mes_solto, alternativa, trecho (até 3 pedaços, " · ") e quando (da data)."""
    hoje = hoje or date.today()
    out: dict = {}
    trechos: list[str] = []
    for texto, quando in mensagens:
        r = ler_texto(texto, hoje)
        if not r:
            continue
        for k in ("data", "data_trecho", "alternativa", "tipo", "convidados", "mes_solto"):
            if k in r:
                out[k] = r[k]
        if "data" in r:
            out["quando"] = quando
        if r.get("trecho"):
            trechos.append(r["trecho"])
    if trechos:
        out["trecho"] = " · ".join(trechos[-3:])
    if "data" in out:
        out.pop("mes_solto", None)
    return out


def _aware(dt):
    if isinstance(dt, datetime):
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def ler_conversa(pool, conta_id: int, lead_id: int, agora: datetime | None = None) -> dict:
    """Lê as mensagens do cliente desse lead e aplica no card. Devolve
    {"preencheu": [...campos...], "pista": str|None}. Nunca estoura."""
    agora = agora or datetime.now(timezone.utc)
    hoje = agora.date()
    try:
        with pool.connection() as c:
            atual = c.execute(
                """select evento_em, evento_tipo, evento_convidados, evento_pista
                     from prospeccao where id=%s and conta_id=%s""",
                (lead_id, conta_id)).fetchone()
            if not atual:
                return {"preencheu": [], "pista": None}
            rows = c.execute(
                """select m.texto, m.criado_em from conversas cv
                     join mensagens m on m.conversa_id = cv.id
                    where cv.prospeccao_id=%s and cv.conta_id=%s
                      and m.direcao='in' and coalesce(m.texto,'') <> ''
                    order by m.criado_em desc, m.id desc limit 80""",
                (lead_id, conta_id)).fetchall()
            lido = ler_mensagens(list(reversed(rows)), hoje)
            ev_em, ev_tipo, ev_conv, _pista_antiga = atual
            novos = {}
            if ev_em is None and lido.get("data"):
                novos["data"] = lido["data"]
            if not ev_tipo and lido.get("tipo"):
                novos["tipo"] = lido["tipo"]
            if not ev_conv and lido.get("convidados"):
                novos["convidados"] = lido["convidados"]
            preencheu = []
            if novos and _evl.gravar(c, conta_id, lead_id, novos, so_vazios=True,
                                     origem="conversa", trecho=lido.get("trecho")):
                preencheu = sorted(novos)
            # A PISTA: o que ouviu e não gravou. Sem dia, o mês; com data no lead e
            # outra na conversa, a da conversa — quem muda é o vendedor.
            pista = None
            data_lead = ev_em or novos.get("data")
            if data_lead is None and lido.get("mes_solto"):
                pista = "falou de " + lido["mes_solto"]
            elif ev_em is not None and lido.get("data") and lido["data"] != ev_em:
                pista = "falou de " + _evl.data_curta(lido["data"], hoje)
            elif "data" in novos and lido.get("alternativa"):
                pista = "disse " + lido["alternativa"]
            c.execute("update prospeccao set evento_pista=%s, evento_lido_em=%s "
                      "where id=%s and conta_id=%s", (pista, agora, lead_id, conta_id))
            c.commit()
            return {"preencheu": preencheu, "pista": pista}
    except Exception:  # noqa: BLE001
        _log.warning("leitor: não li a conversa do lead %s", lead_id, exc_info=True)
        return {"preencheu": [], "pista": None}


def ler_conversa_bg(pool, conta_id: int, conversa_id: int) -> None:
    """Entrada dos webhooks: da conversa pro lead, só em conta que vende data.
    Best-effort — nunca estoura."""
    try:
        from finance import vendas as _vendas
        if not _vendas.vende_data(pool, conta_id):
            return
        with pool.connection() as c:
            r = c.execute("select prospeccao_id from conversas where id=%s and conta_id=%s",
                          (conversa_id, conta_id)).fetchone()
        if r and r[0]:
            ler_conversa(pool, conta_id, int(r[0]))
    except Exception:  # noqa: BLE001
        _log.warning("leitor: falhou na conversa %s", conversa_id, exc_info=True)


def leads_por_ler(c, conta_id: int) -> list[int]:
    """Quem ainda tem campo vazio E tem mensagem do cliente mais nova que a última
    leitura — o acervo do botão "Ler as conversas"."""
    return [r[0] for r in c.execute(
        """select p.id from prospeccao p
            where p.conta_id=%s and p.estagio='lead' and p.status <> 'perdido'
              and (p.evento_em is null or p.evento_tipo is null or p.evento_convidados is null)
              and exists (select 1 from conversas cv join mensagens m on m.conversa_id=cv.id
                           where cv.prospeccao_id=p.id and m.direcao='in'
                             and coalesce(m.texto,'') <> ''
                             and m.criado_em > coalesce(p.evento_lido_em, '-infinity'::timestamptz))
            order by p.id""", (conta_id,)).fetchall()]


def ler_acervo(pool, conta_id: int, ids: list[int]) -> dict:
    """O botão: lê cada lead da lista. Devolve contagens do que preencheu."""
    tot = {"lidos": 0, "data": 0, "tipo": 0, "convidados": 0, "pista": 0}
    for lid in ids:
        r = ler_conversa(pool, conta_id, lid)
        tot["lidos"] += 1
        for k in r["preencheu"]:
            tot[k] = tot.get(k, 0) + 1
        if r["pista"]:
            tot["pista"] += 1
    _log.info("leitor: acervo conta=%s %s", conta_id, tot)
    return tot
