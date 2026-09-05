"""O Raio-X do vendedor: sua semana, responda hoje, fechamentos, e a confiança
do dado. E a mensagem de segunda no grupo.

POR QUE EXISTE (Raio-X da Prime Eventos, 05/09/2026, docs/mockups/raio_x_como_fica.html)
Em 24 dias a Prime teve 283 leads, 2 contratos assinados e R$ 107 mil parados em
propostas — 9 delas nunca saíram do rascunho. A mediana da primeira resposta ao
lead novo era 2h30 (o mercado mede 5 minutos), e metade das conversas parava na
primeira tentativa. Nada disso estava numa tela: o vendedor via a Fila, o dono via
o funil, e o número que decide a venda (quanto tempo até responder, quantas vezes
insistiu) ninguém via.

O QUE ESTE MÓDULO FAZ, E O QUE NÃO FAZ
Só LÊ o que já existe: `prospeccao`, `conversas`, `mensagens`, `orcamentos`,
`contratos`, `eventos_agenda`, `wa_qr_log` e `wa_decifra_diario`. Não grava nada
sobre lead ou conversa. As duas tabelas dele (`raio_x_config`, `raio_x_envios`,
migração 207) são a escolha do grupo e a trava de envio.

A CONFIANÇA DO DADO É PARTE DO NÚMERO. Quando o vendedor responde pelo celular, o
Zaq só fica sabendo pelo eco que o WhatsApp devolve; se o eco falha, a resposta
existiu e o Zaq não viu, e o tempo de resposta fica inflado. Por isso todo Raio-X
carrega no pé quantos dias foram medidos, quantas vezes a conexão religou e quais
mensagens podem não ter chegado, pelo nome do cliente. Número sem esse rodapé não
vai pro grupo (regra do mockup, aprovada em 05/09).

TEMPO: tudo em America/Sao_Paulo, como o resto do Cockpit (cockpit_dono._brt).
"""
from __future__ import annotations

import logging
import re
import statistics
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

_log = logging.getLogger("finance.raio_x")
_TZ = ZoneInfo("America/Sao_Paulo")

#: 1ª resposta ao lead novo: a meta do mercado (Chili Piper, InsideSales, e 63%
#: dos brasileiros esperam isso no WhatsApp — Maxbot/Unnica).
META_PRIMEIRA_MIN = 5
#: festa a menos de X dias sem proposta é urgência
FESTA_PERTO_DIAS = 60
#: a cadência de retorno: 3h · 24h · 3 dias · 7 dias. O toque N vence quando a
#: última mensagem nossa tem pelo menos CADENCIA[N-1] dias sem resposta.
CADENCIA_DIAS = (0.125, 1, 3, 7)
#: depois do 4º toque sem resposta, o lead é parado (porta aberta), não fila
MAX_TOQUES = 4
#: status de lead que ainda estão em jogo
ABERTOS = ("novo", "contatado", "qualificado", "proposta")

# O cliente se despediu: não é pergunta, é fechamento. Fica fora do "responda
# hoje" (mas continua na Fila normal, como sempre).
_DESPEDIDA = re.compile(
    r"^\s*(ok|okay|okk+|certo|certinho|tá|ta|tá bom|ta bom|tá certo|ta certo|sim|obrigad[ao]s?!*|"
    r"muito obrigad[ao]|perfeito|pode ser|entendi|fechado|show|beleza|combinado|top|maravilha|"
    r"está bem|esta bem|blz|vlw|valeu|👍|🙏|❤️|😊)[\s!.,)]*(\S{0,3})?\s*$", re.I)


def agora_brt(agora: datetime | None = None) -> datetime:
    if agora is None:
        return datetime.now(_TZ)
    return agora.astimezone(_TZ) if agora.tzinfo else agora.replace(tzinfo=_TZ)


# ---------------------------------------------------------------- a janela

def janela(periodo: str, agora: datetime | None = None) -> tuple[datetime, datetime, str]:
    """(início, fim, rótulo) do período. 'semana' começa na segunda 00:00 e vai
    até agora; 'passada' é a semana anterior fechada (o que o grupo recebe na
    segunda); 'mes' é dia 1; 'tudo' são 365 dias."""
    a = agora_brt(agora)
    hoje0 = a.replace(hour=0, minute=0, second=0, microsecond=0)
    segunda = hoje0 - timedelta(days=hoje0.weekday())
    if periodo == "passada":
        ini, fim = segunda - timedelta(days=7), segunda
    elif periodo == "mes":
        ini, fim = hoje0.replace(day=1), a
    elif periodo == "tudo":
        ini, fim = hoje0 - timedelta(days=365), a
    else:
        ini, fim = segunda, a
    ult = (fim - timedelta(seconds=1)) if periodo == "passada" else fim
    rot = (f"{ini:%d/%m} a {ult:%d/%m}" if periodo in ("semana", "passada")
           else f"{ini:%d/%m} a {ult:%d/%m}")
    return ini, fim, rot


def _anterior(ini: datetime, fim: datetime) -> tuple[datetime, datetime]:
    return ini - (fim - ini), ini


def _mediana(xs: list[float]) -> float | None:
    return round(statistics.median(xs)) if xs else None


def fmt_min(m: float | None) -> str:
    """'9 min', '2h40', '—'."""
    if m is None:
        return "—"
    m = int(round(m))
    if m < 60:
        return f"{m} min"
    h, r = divmod(m, 60)
    if h < 48:
        return f"{h}h{r:02d}"
    return f"{h // 24} dias"


def _reais(centavos) -> str:
    v = int(centavos or 0) // 100
    return f"R$ {v:,}".replace(",", ".")


# ---------------------------------------------------------------- sua semana

def _primeiras_respostas(c, conta_id: int, membro_id: int, ini, fim) -> list[float]:
    rows = c.execute("""
        with prim as (
          select cv.id,
                 min(ms.criado_em) filter (where ms.direcao = 'in') as pin,
                 min(ms.criado_em) filter (where ms.direcao = 'out' and ms.autor = 'humano') as pout
            from conversas cv
            join prospeccao p on p.id = cv.prospeccao_id
            join mensagens ms on ms.conversa_id = cv.id
           where cv.conta_id = %s and p.vendedor_id = %s
             and cv.criado_em >= %s and cv.criado_em < %s
           group by cv.id)
        select extract(epoch from pout - pin) / 60
          from prim where pin is not null and pout > pin""",
        (conta_id, membro_id, ini, fim)).fetchall()
    return [float(r[0]) for r in rows]


def sua_semana(pool, conta_id: int, membro_id: int, ini: datetime, fim: datetime) -> dict:
    """Os números do período pra UM vendedor. Tudo em uma conexão, consultas
    pequenas (o vendedor tem dezenas de leads, não milhares)."""
    ant_ini, ant_fim = _anterior(ini, fim)
    with pool.connection() as c:
        leads, com_data, sem_tipo = c.execute("""
            select count(*),
                   count(*) filter (where evento_em is not null),
                   count(*) filter (where evento_tipo is null or evento_tipo = '')
              from prospeccao
             where conta_id = %s and vendedor_id = %s and criado_em >= %s and criado_em < %s""",
            (conta_id, membro_id, ini, fim)).fetchone()
        resp = _primeiras_respostas(c, conta_id, membro_id, ini, fim)
        resp_ant = _primeiras_respostas(c, conta_id, membro_id, ant_ini, ant_fim)
        enviadas, rascunhos = c.execute("""
            select count(*) filter (where o.status <> 'rascunho' and o.criado_em >= %s and o.criado_em < %s),
                   count(*) filter (where o.status = 'rascunho')
              from orcamentos o join prospeccao p on p.orcamento_id = o.id
             where p.conta_id = %s and p.vendedor_id = %s""",
            (ini, fim, conta_id, membro_id)).fetchone()
        rascunho_mais_velho = c.execute("""
            select min(o.criado_em) from orcamentos o join prospeccao p on p.orcamento_id = o.id
             where p.conta_id = %s and p.vendedor_id = %s and o.status = 'rascunho'""",
            (conta_id, membro_id)).fetchone()[0]
        # toque = mensagem nossa mandada quando a anterior da conversa também era
        # nossa (o cliente não tinha respondido). É o "insistiu".
        toques = c.execute("""
            with seq as (
              select ms.direcao, ms.autor, ms.criado_em,
                     lag(ms.direcao) over (partition by ms.conversa_id order by ms.criado_em, ms.id) as ant
                from conversas cv join prospeccao p on p.id = cv.prospeccao_id
                join mensagens ms on ms.conversa_id = cv.id
               where cv.conta_id = %s and p.vendedor_id = %s
                 and ms.criado_em >= %s - interval '30 days' and ms.criado_em < %s)
            select count(*) from seq
             where direcao = 'out' and autor = 'humano' and ant = 'out'
               and criado_em >= %s""", (conta_id, membro_id, ini, fim, ini)).fetchone()[0]
        # parou na 1ª tentativa: a última mensagem é nossa, só uma depois da última
        # do cliente, e já faz mais de 24h. Vale pros leads em jogo.
        paradas = c.execute("""
            with in_ as (
              select cv.id as cid,
                     max(ms.criado_em) filter (where ms.direcao = 'in') as ult_in,
                     max(ms.criado_em) as ult
                from conversas cv join prospeccao p on p.id = cv.prospeccao_id
                join mensagens ms on ms.conversa_id = cv.id
               where cv.conta_id = %s and p.vendedor_id = %s and p.status = any(%s)
               group by cv.id)
            select count(*) from in_
             where ult > coalesce(ult_in, '2000-01-01') and ult < %s - interval '24 hours'
               and (select count(*) from mensagens m where m.conversa_id = in_.cid
                      and m.direcao = 'out' and m.criado_em > coalesce(in_.ult_in, '2000-01-01')) = 1""",
            (conta_id, membro_id, list(ABERTOS), fim)).fetchone()[0]
        assinados = c.execute("""
            select coalesce(nullif(o.cliente, ''), p.contato, p.empresa, 'cliente'), c.valor_centavos, c.assinado_em
              from contratos c join orcamentos o on o.id = c.orcamento_id
              join prospeccao p on p.orcamento_id = o.id
             where c.conta_id = %s and p.vendedor_id = %s and c.status = 'assinado'
               and c.assinado_em >= %s and c.assinado_em < %s
             order by c.assinado_em desc""", (conta_id, membro_id, ini, fim)).fetchall()
        sem_assinar = c.execute("""
            select coalesce(nullif(o.cliente, ''), p.contato, p.empresa, 'cliente'), o.primeiro_ano_centavos, o.aprovada_em
              from orcamentos o join prospeccao p on p.orcamento_id = o.id
             where p.conta_id = %s and p.vendedor_id = %s and o.aprovada_em is not null
               and not exists (select 1 from contratos c where c.orcamento_id = o.id and c.status = 'assinado')
             order by o.aprovada_em""", (conta_id, membro_id)).fetchall()
    n_5 = sum(1 for m in resp if m <= META_PRIMEIRA_MIN)
    return {
        "ini": ini, "fim": fim,
        "leads": int(leads), "leads_com_data": int(com_data), "leads_sem_tipo": int(sem_tipo),
        "primeira_min": _mediana(resp), "primeira_n": len(resp), "primeira_em_5": n_5,
        "primeira_min_anterior": _mediana(resp_ant),
        "propostas_enviadas": int(enviadas), "rascunhos": int(rascunhos),
        "rascunho_dias": ((fim - rascunho_mais_velho).days if rascunho_mais_velho else 0),
        "toques": int(toques), "paradas_1a": int(paradas),
        "contratos": [{"nome": n, "valor_centavos": int(v or 0), "em": em} for n, v, em in assinados],
        "contratos_valor": sum(int(v or 0) for _, v, _ in assinados),
        "sem_assinar": [{"nome": n, "valor_centavos": int(v or 0), "dias": (fim - em).days if em else 0}
                        for n, v, em in sem_assinar],
    }


def cor(metrica: str, s: dict) -> str:
    """ok / amb / ruim, pra cada uma das quatro metas. A régua é a do mockup."""
    if metrica == "primeira":
        m = s["primeira_min"]
        if m is None:
            return "amb"
        return "ok" if m <= META_PRIMEIRA_MIN else ("amb" if m <= 60 else "ruim")
    if metrica == "propostas":
        if s["rascunhos"] == 0:
            return "ok"
        return "ruim" if s["rascunho_dias"] > 1 else "amb"
    if metrica == "toques":
        return "ok" if s["paradas_1a"] == 0 else ("amb" if s["paradas_1a"] <= 5 else "ruim")
    if metrica == "contratos":
        if s["contratos"]:
            return "ok"
        return "amb" if s["sem_assinar"] else "amb"
    return "amb"


# ---------------------------------------------------------------- responda hoje

def _ordinal(n: int) -> str:
    return f"{n}º"


def responda_hoje(pool, conta_id: int, membro_id: int, agora: datetime | None = None,
                  base: str = "/cockpit") -> dict:
    """A fila do que mais urge, em quatro faixas, na ordem em que aparecem:

      pergunta   o cliente falou por último e não foi despedida
      festa      festa em até 60 dias, sem proposta
      toque      a última mensagem é nossa e o toque N da cadência venceu
      visita     visita amanhã, sem desfecho

    Um lead entra numa faixa só (a primeira em que cabe). `sem_urgencia` é o resto
    dos leads em jogo — a Fila normal continua com todos."""
    a = agora_brt(agora)
    hoje = a.date()
    with pool.connection() as c:
        leads = c.execute("""
            select p.id, coalesce(nullif(p.contato, ''), nullif(p.empresa, ''), 'lead') as nome,
                   p.status, p.evento_em, p.evento_tipo, p.orcamento_id,
                   u.direcao, u.texto, u.criado_em, coalesce(u.cauda_out, 0)
              from prospeccao p
              left join lateral (
                select ms.direcao, left(coalesce(ms.texto, ''), 120) as texto, ms.criado_em,
                       (select count(*) from mensagens m2
                         where m2.conversa_id = cv.id and m2.direcao = 'out'
                           and m2.criado_em > coalesce((select max(m3.criado_em) from mensagens m3
                                                          where m3.conversa_id = cv.id and m3.direcao = 'in'),
                                                       '2000-01-01')) as cauda_out
                  from conversas cv join mensagens ms on ms.conversa_id = cv.id
                 where cv.prospeccao_id = p.id
                 order by ms.criado_em desc, ms.id desc limit 1) u on true
             where p.conta_id = %s and p.vendedor_id = %s and p.status = any(%s)""",
            (conta_id, membro_id, list(ABERTOS))).fetchall()
        visitas = c.execute("""
            select e.id, e.prospeccao_id, e.titulo, e.inicio, coalesce(nullif(p.contato,''), nullif(p.empresa,''), e.titulo)
              from eventos_agenda e join prospeccao p on p.id = e.prospeccao_id
             where e.conta_id = %s and p.vendedor_id = %s and e.status = 'ativo' and e.desfecho is null
               and (e.inicio at time zone 'America/Sao_Paulo')::date = %s
             order by e.inicio""", (conta_id, membro_id, hoje + timedelta(days=1))).fetchall()
    itens: list[dict] = []
    usados: set[int] = set()

    def _add(faixa, lid, nome, detalhe, acao, ordem, href=None):
        usados.add(lid)
        itens.append({"faixa": faixa, "lead_id": lid, "nome": nome, "detalhe": detalhe,
                      "acao": acao, "ordem": ordem, "href": href or f"{base}/lead/{lid}"})

    for lid, nome, status, ev_em, ev_tipo, orc, dirc, texto, em, cauda in leads:
        em_brt = em.astimezone(_TZ) if em else None
        if dirc == "in" and texto is not None and not _DESPEDIDA.match(texto or "") and em_brt:
            horas = (a - em_brt).total_seconds() / 3600
            quando = (f"{int(horas)}h" if horas < 48 else f"{int(horas // 24)} dias")
            t = (texto or "").strip() or "mensagem"
            _add("pergunta", lid, nome, f"“{t[:70]}” · {quando}", "responder", -horas)
    for lid, nome, status, ev_em, ev_tipo, orc, dirc, texto, em, cauda in leads:
        if lid in usados or not ev_em or orc:
            continue
        dias = (ev_em - hoje).days
        if 0 <= dias <= FESTA_PERTO_DIAS and status in ("novo", "contatado", "qualificado"):
            tipo = f"{ev_tipo} " if ev_tipo else "Festa "
            _add("festa", lid, nome, f"{tipo}{ev_em:%d/%m} · em {dias} dias · sem proposta", "proposta", dias)
    for lid, nome, status, ev_em, ev_tipo, orc, dirc, texto, em, cauda in leads:
        if lid in usados or dirc != "out" or not em:
            continue
        n = int(cauda)
        if n < 1 or n >= MAX_TOQUES:
            continue
        dias = (a - em.astimezone(_TZ)).total_seconds() / 86400
        if dias >= CADENCIA_DIAS[n]:
            prox = n + 1
            rot = "porta aberta" if prox == MAX_TOQUES else f"{_ordinal(prox)} toque"
            _add("toque", lid, nome, f"sem resposta há {int(dias)} dia(s) · {n} toque(s) feito(s)", rot, -dias)
    for eid, lid, titulo, inicio, nome in visitas:
        _add("visita", lid, nome, f"visita amanhã {inicio.astimezone(_TZ):%H:%M} · confirmar na véspera",
             "confirmar", 0, href=f"{base}/lead/{lid}")
    ordem_faixa = {"pergunta": 0, "festa": 1, "toque": 2, "visita": 3}
    itens.sort(key=lambda i: (ordem_faixa[i["faixa"]], i["ordem"]))
    return {"itens": itens, "n": len(itens),
            "por_faixa": {f: sum(1 for i in itens if i["faixa"] == f) for f in ordem_faixa},
            "sem_urgencia": sum(1 for l in leads if l[0] not in usados)}


# ---------------------------------------------------------------- fechamentos

def fechamentos(pool, conta_id: int, membro_id: int, agora: datetime | None = None) -> dict:
    a = agora_brt(agora)
    with pool.connection() as c:
        rows = c.execute("""
            select o.id, coalesce(nullif(o.cliente, ''), p.contato, p.empresa, 'cliente'), o.status,
                   o.primeiro_ano_centavos, o.criado_em, o.aprovada_em, o.sinal_pago_em, p.evento_em, p.evento_tipo,
                   c.status, c.assinado_em, c.valor_centavos, c.enviado_em, p.id,
                   (select count(*) from conversas cv join mensagens m on m.conversa_id = cv.id
                     where cv.prospeccao_id = p.id and m.direcao = 'out'
                       and m.criado_em > coalesce((select max(m3.criado_em) from conversas c3 join mensagens m3 on m3.conversa_id = c3.id
                                                     where c3.prospeccao_id = p.id and m3.direcao = 'in'), '2000-01-01'))
              from orcamentos o join prospeccao p on p.orcamento_id = o.id
              left join contratos c on c.orcamento_id = o.id
             where p.conta_id = %s and p.vendedor_id = %s
             order by o.criado_em desc""", (conta_id, membro_id)).fetchall()
    assinou, falta, esperando, rascunhos = [], [], [], []
    for (oid, nome, st, total, criado, aprov, sinal, ev_em, ev_tipo, cst, cass, cval, cenv, lid, toques) in rows:
        festa = f"{ev_tipo or 'festa'} {ev_em:%d/%m}" if ev_em else (ev_tipo or "")
        base = {"orcamento_id": oid, "lead_id": lid, "nome": nome, "festa": festa,
                "valor_centavos": int((cval if cst == "assinado" else total) or 0)}
        if cst == "assinado" and cass:
            if (a - cass.astimezone(_TZ)).days <= 30:
                assinou.append(dict(base, em=cass, detalhe=f"assinado {cass.astimezone(_TZ):%d/%m}"
                                    + (" · sinal pago" if sinal else "")))
        elif aprov:
            dias = (a - aprov.astimezone(_TZ)).days
            falta.append(dict(base, dias=dias, sinal=bool(sinal),
                              detalhe=("sinal pago · " if sinal else "") + f"contrato sem assinatura há {dias} dia(s)",
                              acao="reenviar o contrato"))
        elif st == "enviado":
            dias = (a - criado.astimezone(_TZ)).days
            esperando.append(dict(base, dias=dias, toques=int(toques),
                                  detalhe=f"enviada há {dias} dia(s) · {int(toques)} toque(s)",
                                  acao=f"{_ordinal(int(toques) + 1)} toque" if toques < MAX_TOQUES else "porta aberta"))
        elif st == "rascunho":
            dias = (a - criado.astimezone(_TZ)).days
            urgente = bool(ev_em) and (ev_em - a.date()).days <= 30
            esc = " · a festa é em " + str((ev_em - a.date()).days) + " dias" if urgente else ""
            rascunhos.append(dict(base, dias=dias, urgente=urgente,
                                  detalhe=f"montada há {dias} dia(s), nunca enviada{esc}", acao="enviar agora"))
    em_jogo = sum(x["valor_centavos"] for x in falta + esperando + rascunhos)
    return {"assinou": assinou, "falta_assinar": falta, "esperando": esperando, "rascunhos": rascunhos,
            "em_jogo_centavos": em_jogo}


# ---------------------------------------------------------------- confiança do dado

def confianca(pool, conta_id: int, ini: datetime, fim: datetime, agora: datetime | None = None) -> dict:
    """Quantos dias o Zaq mediu, quantas vezes a conexão religou (o wa_qr_log
    guarda 48h, então é "nas últimas 48h"), e quais mensagens de cliente podem
    não ter chegado — pelo nome. Tolerante: sem as tabelas, devolve o que dá."""
    a = agora_brt(agora)
    out = {"dias_periodo": max(1, (fim.date() - ini.date()).days + 1), "dias_medidos": None,
           "religou": None, "nao_chegaram": [], "nao_chegaram_fechados": 0}
    try:
        with pool.connection() as c:
            out["dias_medidos"] = c.execute("""
                select count(distinct (ms.criado_em at time zone 'America/Sao_Paulo')::date)
                  from conversas cv join mensagens ms on ms.conversa_id = cv.id
                 where cv.conta_id = %s and ms.criado_em >= %s and ms.criado_em < %s""",
                (conta_id, ini, fim)).fetchone()[0]
    except Exception as e:  # noqa: BLE001
        _log.info("confiança: dias medidos falhou: %s: %s", type(e).__name__, e)
    try:
        with pool.connection() as c:
            desde = max(ini, a - timedelta(hours=48))
            out["religou"] = c.execute(
                "select count(*) from wa_qr_log where conta_id = %s and msg = 'conexão fechou' and criado_em >= %s",
                (conta_id, desde)).fetchone()[0]
            falhas = c.execute("""
                select distinct coalesce(l.dados->'key'->>'id', l.dados->>'id') as id,
                       coalesce(l.dados->'key'->>'senderPn', l.dados->>'senderPn',
                                l.dados->'key'->>'remoteJid', l.dados->>'remoteJid') as de
                  from wa_qr_log l
                 where l.conta_id = %s and l.msg = 'failed to decrypt message' and l.criado_em >= %s
                   and coalesce(l.dados->>'fromMe', l.dados->'key'->>'fromMe') = 'false'
                   and coalesce(l.dados->'key'->>'remoteJid', l.dados->>'remoteJid', '') not like '%%@g.us'
                   and coalesce(l.dados->'key'->>'remoteJid', l.dados->>'remoteJid', '') not like 'status@%%'
                   and coalesce(l.dados->'key'->>'remoteJid', l.dados->>'remoteJid', '') not like '%%@newsletter'
                   and coalesce(l.dados->'key'->>'id', l.dados->>'id') is not null""",
                (conta_id, desde)).fetchall()
            ids = [f[0] for f in falhas]
            chegaram = set()
            if ids:
                chegaram = {r[0] for r in c.execute(
                    """select ms.provider_sid from mensagens ms join conversas cv on cv.id = ms.conversa_id
                        where cv.conta_id = %s and ms.provider_sid = any(%s)""", (conta_id, ids)).fetchall()}
            nomes: dict[str, str] = {}
            for fid, de in falhas:
                if fid in chegaram:
                    continue
                numero = re.sub(r"@.*$", "", de or "")
                if not numero or numero in nomes:
                    continue
                r = c.execute("""
                    select coalesce(nullif(p.contato, ''), nullif(p.empresa, ''), nullif(cv.contato_nome, ''))
                      from conversas cv left join prospeccao p on p.id = cv.prospeccao_id
                     where cv.conta_id = %s and cv.contato_ref = %s limit 1""", (conta_id, numero)).fetchone()
                nomes[numero] = (r[0] if r and r[0] else f"…{numero[-4:]}")
            out["nao_chegaram"] = [{"numero": n, "nome": nm, "mensagens": sum(1 for f in falhas if f[0] not in chegaram and re.sub(r"@.*$", "", f[1] or "") == n)}
                                   for n, nm in nomes.items()]
            try:
                out["nao_chegaram_fechados"] = int(c.execute("""
                    select coalesce(sum(nunca_chegaram), 0) from wa_decifra_diario
                     where conta_id = %s and from_me = false and dia >= %s and dia < %s""",
                    (conta_id, ini.date(), (a - timedelta(hours=48)).date())).fetchone()[0] or 0)
            except Exception:  # noqa: BLE001
                pass
    except Exception as e:  # noqa: BLE001
        _log.info("confiança: log falhou: %s: %s", type(e).__name__, e)
    return out


def texto_confianca(cf: dict) -> str:
    partes = []
    if cf.get("dias_medidos") is not None:
        partes.append(f"{cf['dias_medidos']} de {cf['dias_periodo']} dias medidos")
    if cf.get("religou") is not None:
        partes.append(f"conexão religou {cf['religou']}× nas últimas 48h")
    n = sum(x["mensagens"] for x in cf.get("nao_chegaram") or []) + int(cf.get("nao_chegaram_fechados") or 0)
    if n:
        quem = ", ".join(x["nome"] for x in cf.get("nao_chegaram") or [])
        partes.append(f"{n} mensagem(ns) pode(m) não ter chegado" + (f" ({quem}): confira no celular" if quem else ""))
    else:
        partes.append("nenhuma mensagem perdida")
    return " · ".join(partes)


# ---------------------------------------------------------------- o grupo

def _primeiro_nome(nome: str) -> str:
    return (nome or "—").strip().split(" ")[0].capitalize()


def texto_grupo(pool, conta_id: int, ini: datetime, fim: datetime, agora: datetime | None = None,
                rotulo: str | None = None) -> str:
    """A mensagem de segunda: uma linha por vendedor, a linha da empresa, e a
    confiança do dado. Curta de propósito: cabe numa tela sem rolar muito; o
    detalhe está no app."""
    a = agora_brt(agora)
    with pool.connection() as c:
        vend = c.execute("""
            select id, nome from membros
             where conta_id = %s and papel = 'vendedor' and coalesce(ativo, true)
             order by nome""", (conta_id,)).fetchall()
        nome_empresa = (c.execute("select coalesce(nullif(nome_fantasia,''), nome) from contas where id = %s",
                                  (conta_id,)).fetchone() or ["a empresa"])[0]
    rot = rotulo or janela("passada", a)[2]
    linhas = [f"🔎 *Raio-X da semana · {rot}*", ""]
    tot = {"leads": 0, "resp": [], "env": 0, "rasc": 0, "hoje": 0, "contratos": 0, "valor": 0, "em_jogo": 0}
    for mid, nome in vend:
        s = sua_semana(pool, conta_id, mid, ini, fim)
        h = responda_hoje(pool, conta_id, mid, a)
        f = fechamentos(pool, conta_id, mid, a)
        tot["leads"] += s["leads"]; tot["env"] += s["propostas_enviadas"]; tot["rasc"] += s["rascunhos"]
        tot["hoje"] += h["n"]; tot["contratos"] += len(s["contratos"]); tot["valor"] += s["contratos_valor"]
        tot["em_jogo"] += f["em_jogo_centavos"]
        tot["resp"].extend(_primeiras_respostas_cache(pool, conta_id, mid, ini, fim))
        partes = [f"{s['leads']} leads",
                  f"1ª resposta {fmt_min(s['primeira_min'])}" + (f" ({s['primeira_em_5']} em 5 min)" if s["primeira_n"] else ""),
                  f"propostas {s['propostas_enviadas']} enviada(s)" + (f", *{s['rascunhos']} rascunho* ⚠️" if s["rascunhos"] else ""),
                  f"responda hoje: *{h['n']}*"]
        if s["contratos"]:
            partes.append("contrato: " + ", ".join(_primeiro_nome(x["nome"]) for x in s["contratos"]) + " 🎉")
        elif s["sem_assinar"]:
            x = s["sem_assinar"][0]
            partes.append(f"{_primeiro_nome(x['nome'])}: {x['dias']} dia(s) sem assinatura")
        linhas.append(f"*{_primeiro_nome(nome)}* · " + " · ".join(partes))
    linhas.append("")
    linhas.append(f"*{nome_empresa} na semana:* {tot['leads']} leads · 1ª resposta {fmt_min(_mediana(tot['resp']))} "
                  f"(meta {META_PRIMEIRA_MIN} min) · {tot['env']} proposta(s) enviada(s)"
                  + (f" · {tot['rasc']} em rascunho" if tot["rasc"] else "")
                  + f" · {tot['contratos']} contrato(s)" + (f" ({_reais(tot['valor'])})" if tot["valor"] else "")
                  + (f" · {_reais(tot['em_jogo'])} em propostas abertas" if tot["em_jogo"] else ""))
    linhas.append("")
    linhas.append("📡 " + texto_confianca(confianca(pool, conta_id, ini, fim, a)))
    return "\n".join(linhas)


def _primeiras_respostas_cache(pool, conta_id, membro_id, ini, fim):
    with pool.connection() as c:
        return _primeiras_respostas(c, conta_id, membro_id, ini, fim)


# ---------------------------------------------------------------- config e envio

def config(pool, conta_id: int) -> dict | None:
    try:
        with pool.connection() as c:
            r = c.execute("select grupo_jid, grupo_nome, ativo, atualizado_em from raio_x_config where conta_id = %s",
                          (conta_id,)).fetchone()
            ult = c.execute("""select semana, enviado_em, erro from raio_x_envios
                                where conta_id = %s order by semana desc limit 1""", (conta_id,)).fetchone()
    except Exception as e:  # noqa: BLE001
        _log.info("raio_x.config: %s: %s", type(e).__name__, e)
        return None
    if not r and not ult:
        return None
    return {"grupo_jid": r[0] if r else None, "grupo_nome": r[1] if r else None,
            "ativo": bool(r[2]) if r else False,
            "ultimo": ({"semana": ult[0], "enviado_em": ult[1], "erro": ult[2]} if ult else None)}


def definir(pool, conta_id: int, grupo_jid: str | None, grupo_nome: str | None, ativo: bool) -> dict:
    jid = (grupo_jid or "").strip()
    if jid and not jid.endswith("@g.us"):
        return {"ok": False, "erro": "grupo_invalido"}
    with pool.connection() as c:
        c.execute("""insert into raio_x_config (conta_id, grupo_jid, grupo_nome, ativo, atualizado_em)
                     values (%s, %s, %s, %s, now())
                     on conflict (conta_id) do update
                        set grupo_jid = excluded.grupo_jid, grupo_nome = excluded.grupo_nome,
                            ativo = excluded.ativo, atualizado_em = now()""",
                  (conta_id, jid or None, (grupo_nome or "").strip() or None, bool(ativo)))
        c.commit()
    return {"ok": True}


def _enviar_padrao(pool, conta_id: int, jid: str, texto: str) -> dict:
    from finance import whatsapp_out as wo
    with pool.connection() as c:
        if wo.provedor_da_conta(c, conta_id) != "qr":
            # grupo é coisa do WhatsApp da própria empresa; Twilio e Cloud API não
            # falam com grupo
            return {"ok": False, "erro": "so_numero_proprio"}
        return wo.enviar(c, conta_id, jid, texto)


def enviar_agora(pool, conta_id: int, agora: datetime | None = None, enviar=None) -> dict:
    """O botão "Mandar agora" do painel: a semana corrente, no grupo escolhido.
    Não passa pela trava de segunda (é um teste pedido pelo dono)."""
    cfg = config(pool, conta_id)
    if not cfg or not cfg.get("grupo_jid"):
        return {"ok": False, "erro": "sem_grupo"}
    ini, fim, rot = janela("semana", agora)
    texto = texto_grupo(pool, conta_id, ini, fim, agora, rotulo=rot + " (até agora)")
    r = (enviar or _enviar_padrao)(pool, conta_id, cfg["grupo_jid"], texto)
    return dict(r, texto=texto)


def rodar(pool, agora: datetime | None = None, enviar=None) -> int:
    """Chamado pelo ticker do web a cada ~2 min. Só faz alguma coisa na segunda a
    partir das 8h, e uma vez por (conta, semana): a linha em raio_x_envios é a
    trava. Devolve quantos grupos receberam neste ciclo."""
    a = agora_brt(agora)
    if a.weekday() != 0 or a.hour < 8:
        return 0
    semana = a.date()
    ini, fim, rot = janela("passada", a)
    try:
        with pool.connection() as c:
            contas = c.execute("select conta_id, grupo_jid from raio_x_config where ativo and grupo_jid is not null").fetchall()
    except Exception as e:  # noqa: BLE001
        _log.info("raio_x.rodar: sem config: %s: %s", type(e).__name__, e)
        return 0
    enviados = 0
    for conta_id, jid in contas:
        with pool.connection() as c:
            novo = c.execute("""insert into raio_x_envios (conta_id, semana) values (%s, %s)
                                on conflict do nothing returning 1""", (conta_id, semana)).fetchone()
            if not novo:
                # já existe: só tenta de novo se falhou, poucas vezes, com folga
                novo = c.execute("""update raio_x_envios
                                       set tentativas = tentativas + 1, atualizado_em = now()
                                     where conta_id = %s and semana = %s and enviado_em is null
                                       and tentativas < 5 and atualizado_em < now() - interval '10 minutes'
                                     returning 1""", (conta_id, semana)).fetchone()
            c.commit()
        if not novo:
            continue
        try:
            texto = texto_grupo(pool, conta_id, ini, fim, a, rotulo=rot)
            r = (enviar or _enviar_padrao)(pool, conta_id, jid, texto)
        except Exception as e:  # noqa: BLE001
            texto, r = "", {"ok": False, "erro": f"{type(e).__name__}: {e}"[:180]}
        with pool.connection() as c:
            if r.get("ok"):
                c.execute("update raio_x_envios set texto = %s, enviado_em = now(), erro = null, atualizado_em = now() where conta_id = %s and semana = %s",
                          (texto, conta_id, semana))
                enviados += 1
            else:
                c.execute("update raio_x_envios set texto = %s, erro = %s, atualizado_em = now() where conta_id = %s and semana = %s",
                          (texto, str(r.get("erro") or "falha")[:180], conta_id, semana))
                _log.warning("raio_x: envio da conta %s falhou: %s", conta_id, r.get("erro"))
            c.commit()
    return enviados
