"""Cockpit do Dono — métricas e ações pra o dono/gestor acompanhar a equipe.

Mesmo app (/cockpit), mas quem entra como dono/gestor cai numa visão de EQUIPE:
Visão (KPIs + funil do time + precisa de atenção), Placar (ranking dos vendedores)
e Atividade (feed). Ações: reatribuir lead e pausar/reativar vendedor no rodízio.

Tudo escopado por conta_id, lê do dado que já existe (prospeccao/conversas/mensagens/
orcamentos/eventos_agenda). Sem tabela nova.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from finance import funil_regua as _fr

# O painel do dono conta venda pela FASE da etapa, não pelo literal 'ganho' — senão
# o lead que anda pra uma etapa de pós-venda sai do "ganhos do mês" como se a venda
# tivesse sido desfeita (ver finance/funil_regua.sql_fechadas).
_ABERTO_P = "p.status not in " + _fr.sql_encerradas("p")
_ABERTO_T = "status not in " + _fr.sql_encerradas("prospeccao")
_ENCERRADO_P = "p.status in " + _fr.sql_encerradas("p")


def _brt():
    from finance import agenda as ag
    return ag.BRT


def _range(periodo: str):
    """(início, fim) do período em BRT. hoje / semana / mês."""
    now = datetime.now(_brt())
    if periodo == "hoje":
        ini = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif periodo == "mes":
        ini = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:  # semana (default)
        ini = now - timedelta(days=7)
    return ini, now + timedelta(minutes=1)


def _primeiro(nome: str) -> str:
    return (nome or "—").strip().split(" ")[0]


def _reais(centavos) -> str:
    v = int(centavos or 0) // 100
    if v >= 1000:
        return f"R$ {v/1000:.0f} mil" if v % 1000 == 0 or v >= 10000 else f"R$ {v/1000:.1f} mil".replace(".0", "")
    return f"R$ {v}"


#: O último recurso, quando a conta não tem funil lido do banco. NÃO é a lista do
#: funil de ninguém: as etapas de verdade saem de `_etapas_conta`.
_ETAPAS_FUNIL = [("novo", "Novo"), ("contatado", "Contatado"),
                 ("qualificado", "Qualificado"), ("proposta", "Proposta")]

#: QUANTO VALE UM LEAD FECHADO. `valor_estimado_centavos` é o palpite que o
#: vendedor digita na ficha — e está ZERADO na base inteira (ver a nota do
#: relatório de leads em web/painel_relatorios e a do portal). Somando só ele, o
#: cockpit do gestor da Prime mostrava "Fechado no período: R$ 0" com oito
#: contratos assinados na semana, cada um com orçamento e títulos gerados.
#:
#: O valor certo é o do ORÇAMENTO, pela mesma fórmula que gera os títulos
#: (`coalesce(primeiro_ano_centavos, setup_centavos)` — ver finance/cockpit ao
#: fechar contrato e finance/agenda). Assim a tela do gestor diz o mesmo que o
#: contas a receber. O palpite fica como último recurso, pra conta que trabalha
#: sem orçamento.
_VALOR_FECHADO = """coalesce(
        (select coalesce(o.primeiro_ano_centavos, o.setup_centavos)
           from orcamentos o where o.id = p.orcamento_id and o.conta_id = p.conta_id),
        p.valor_estimado_centavos, 0)"""

#: QUANDO FALAMOS COM ESTE LEAD PELA ÚLTIMA VEZ. `ultimo_contato_em` só é escrito
#: por quem move o lead na mão no painel: na Prime ele está preenchido em 34 dos
#: 423 leads abertos, e por isso "parados há +3 dias" virava "cadastrados há mais
#: de 3 dias" — 363 leads, dos quais 118 tinham recebido mensagem nossa nos
#: últimos três dias. A mensagem que saiu é o fato; o campo é o complemento.
_FALOU = """greatest(
        p.ultimo_contato_em,
        (select max(m.criado_em) from conversas cv join mensagens m on m.conversa_id = cv.id
          where cv.prospeccao_id = p.id and cv.conta_id = p.conta_id and m.direcao = 'out'))"""

#: "Parado" cai na ENTRADA do lead quando nunca falamos com ele: quem entrou hoje
#: e ainda não foi atendido não é um lead parado há três dias.
_FALAMOS_EM = "coalesce(" + _FALOU + ", p.criado_em)"

#: "Sem contato hoje" NÃO usa a entrada: lead que chegou hoje e ninguém falou é
#: exatamente o caso que o alerta existe pra pegar. Sem contato nenhum, a data é o
#: começo dos tempos — sempre menor que hoje.
_FALAMOS_OU_NUNCA = "coalesce(" + _FALOU + ", timestamptz 'epoch')"


def _e_gerencia(papel: str) -> bool:
    return (papel or "") in ("dono", "gestor")


# ------------------------------------------------------------------ VISÃO
def visao(pool, conta_id: int, periodo: str = "semana") -> dict:
    ini, fim = _range(periodo)
    with pool.connection() as c:
        novos = c.execute("select count(*) from prospeccao where conta_id=%s and coalesce(estagio,'lead')='lead' "
                          "and criado_em>=%s and criado_em<%s", (conta_id, ini, fim)).fetchone()[0]
        # em atendimento agora (snapshot): leads abertos, split IA x vendedor
        atend = c.execute(
            """select count(*) filter (where coalesce(cv.agente_ativo,true)),
                      count(*) filter (where not coalesce(cv.agente_ativo,true))
                 from prospeccao p
                 left join conversas cv on cv.prospeccao_id=p.id and cv.conta_id=p.conta_id
                where p.conta_id=%s and coalesce(p.estagio,'lead')='lead' and """ + _ABERTO_P + """
                  and p.vendedor_id is not null""", (conta_id,)).fetchone()
        com_ia, com_vend = int(atend[0] or 0), int(atend[1] or 0)
        g = c.execute("select count(*), coalesce(sum(" + _VALOR_FECHADO + "),0) from prospeccao p "
                      "where p.conta_id=%s and p.status in " + _fr.sql_fechadas("p")
                      + " and p.atualizado_em>=%s and p.atualizado_em<%s",
                      (conta_id, ini, fim)).fetchone()
        perd = c.execute("select count(*) from prospeccao where conta_id=%s and status='perdido' "
                         "and atualizado_em>=%s and atualizado_em<%s", (conta_id, ini, fim)).fetchone()[0]
        ganhos, ganhos_c = int(g[0] or 0), int(g[1] or 0)
        conv = round(100 * ganhos / (ganhos + perd)) if (ganhos + perd) else None
        # O FUNIL DO TIME, com as etapas e os nomes DA CONTA (ver `_etapas_conta`).
        # A lista era fixa em quatro chaves e quatro rótulos de fábrica: na Prime o
        # gestor lia "Qualificado" onde o painel diz "Agendado Visita", "Proposta"
        # onde diz "Negociação", e "ORCAMENTO ASSINADO" não aparecia em lugar
        # nenhum. Mesmo defeito que a aba Leads corrigiu em 17/09/2026, na mesma
        # tela, por uma constante esquecida aqui.
        etapas = [(ch, rot) for ch, rot in _etapas_conta(c, conta_id)
                  if ch not in ("ganho", "perdido")] or _ETAPAS_FUNIL
        por_etapa = dict(c.execute(
            "select p.status, count(*) from prospeccao p "
            " where p.conta_id=%s and coalesce(p.estagio,'lead')='lead' group by p.status",
            (conta_id,)).fetchall())
        valor_etapa = dict(c.execute(
            "select p.status, coalesce(sum(" + _VALOR_FECHADO + "),0) from prospeccao p "
            " where p.conta_id=%s and coalesce(p.estagio,'lead')='lead' group by p.status",
            (conta_id,)).fetchall())
        funil = [{"rotulo": rot, "n": int(por_etapa.get(ch) or 0),
                  "valor": _reais(valor_etapa.get(ch) or 0)} for ch, rot in etapas]
        maxn = max([f["n"] for f in funil] + [1])
        for f in funil:
            f["pct"] = round(100 * f["n"] / maxn)
        # precisa de atenção
        agora = datetime.now(_brt())
        parados = c.execute(
            "select count(*) from prospeccao p where p.conta_id=%s and coalesce(p.estagio,'lead')='lead' "
            "and " + _ABERTO_P + " and " + _FALAMOS_EM + " < %s",
            (conta_id, agora - timedelta(days=3))).fetchone()[0]
        quentes = c.execute(
            "select count(*) from prospeccao p where p.conta_id=%s and coalesce(p.estagio,'lead')='lead' "
            "and " + _ABERTO_P + " and p.temperatura='quente' and " + _FALAMOS_OU_NUNCA + " < %s",
            (conta_id, agora.replace(hour=0, minute=0, second=0, microsecond=0))).fetchone()[0]
        propostas = c.execute("select count(*) from orcamentos where conta_id=%s and status='enviado'",
                              (conta_id,)).fetchone()[0]
        visitas = c.execute(
            "select count(*) from eventos_agenda where conta_id=%s and status='ativo' and prospeccao_id is not null "
            "and inicio >= %s and inicio < %s",
            (conta_id, agora.replace(hour=0, minute=0, second=0, microsecond=0),
             agora.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1))).fetchone()[0]
    return {
        "kpis": {"novos": novos, "com_ia": com_ia, "com_vend": com_vend,
                 "ganhos": ganhos, "ganhos_rs": _reais(ganhos_c), "conversao": conv},
        "funil": funil,
        "atencao": {"parados": parados, "quentes": quentes, "propostas": propostas, "visitas": visitas},
    }


# ------------------------------------------------------------------ PLACAR
def _nome_por_id(c, conta_id, mid):
    if not mid:
        return "—"
    try:
        mid_i = int(str(mid).strip())
    except (TypeError, ValueError):
        return "—"
    r = c.execute("select coalesce(nullif(nome,''), email) from membros where id=%s and conta_id=%s",
                  (mid_i, conta_id)).fetchone()
    return r[0] if r else "—"


def placar(pool, conta_id: int, periodo: str = "mes") -> list[dict]:
    ini, fim = _range(periodo)
    d30 = datetime.now(_brt()) - timedelta(days=30)
    out = []
    with pool.connection() as c:
        membros = c.execute(
            "select id, coalesce(nullif(nome,''), email), coalesce(cockpit_pausado,false) "
            "from membros where conta_id=%s and ativo and papel in ('vendedor','gestor','dono') order by id",
            (conta_id,)).fetchall()
        for mid, nome, pausado in membros:
            fila = c.execute("select count(*) from prospeccao where conta_id=%s and vendedor_id=%s "
                             "and coalesce(estagio,'lead')='lead' and " + _ABERTO_T,
                             (conta_id, mid)).fetchone()[0]
            atend = c.execute(
                "select count(*) from conversas cv join prospeccao p on p.id=cv.prospeccao_id "
                "where cv.conta_id=%s and cv.responsavel_membro_id=%s and not coalesce(cv.agente_ativo,true) "
                "and " + _ABERTO_P, (conta_id, mid)).fetchone()[0]
            g = c.execute("select count(*), coalesce(sum(" + _VALOR_FECHADO + "),0) from prospeccao p "
                          "where p.conta_id=%s and p.vendedor_id=%s and p.status in " + _fr.sql_fechadas("p")
                          + " and p.atualizado_em>=%s and p.atualizado_em<%s",
                          (conta_id, mid, ini, fim)).fetchone()
            perd = c.execute("select count(*) from prospeccao where conta_id=%s and vendedor_id=%s and status='perdido' "
                             "and atualizado_em>=%s and atualizado_em<%s", (conta_id, mid, ini, fim)).fetchone()[0]
            ganhos, rs_c = int(g[0] or 0), int(g[1] or 0)
            conv = round(100 * ganhos / (ganhos + perd)) if (ganhos + perd) else None
            resp = c.execute(
                """select avg(extract(epoch from (fo.po - cv.criado_em))/60) from conversas cv
                     join lateral (select min(criado_em) po from mensagens
                                    where conversa_id=cv.id and direcao='out' and autor='humano') fo on true
                    where cv.conta_id=%s and cv.responsavel_membro_id=%s and fo.po is not null and cv.criado_em>=%s""",
                (conta_id, mid, d30)).fetchone()[0]
            out.append({
                "id": mid, "nome": nome, "pausado": bool(pausado),
                "fila": fila, "atendendo": atend, "ganhos": ganhos, "rs_centavos": rs_c, "rs": _reais(rs_c),
                "conversao": (f"{conv}%" if conv is not None else "—"),
                "resp": (f"{round(resp)} min" if resp else "—"),
            })
    out.sort(key=lambda x: (x["rs_centavos"], x["ganhos"], x["fila"]), reverse=True)
    return out


# ------------------------------------------------------------------ VENDEDOR (drill)
def vendedor(pool, conta_id: int, membro_id: int) -> dict | None:
    with pool.connection() as c:
        m = c.execute("select coalesce(nullif(nome,''), email), coalesce(cockpit_pausado,false), papel "
                      "from membros where id=%s and conta_id=%s and ativo", (membro_id, conta_id)).fetchone()
        if not m:
            return None
    base = next((p for p in placar(pool, conta_id) if p["id"] == membro_id), None) or {}
    with pool.connection() as c:
        rows = c.execute(
            """select p.id, p.empresa, p.temperatura, coalesce(cv.agente_ativo, true)
                 from prospeccao p
                 left join conversas cv on cv.prospeccao_id=p.id and cv.conta_id=p.conta_id
                where p.conta_id=%s and p.vendedor_id=%s and coalesce(p.estagio,'lead')='lead'
                  and """ + _ABERTO_P + """
                order by p.atualizado_em desc limit 50""", (conta_id, membro_id)).fetchall()
    from web.painel_prospeccao import TEMP_COR
    leads = [{"id": r[0], "empresa": r[1] or "Lead", "temp_cor": TEMP_COR.get(r[2] or "frio", "#5b9bd5"),
              "ia": bool(r[3])} for r in rows]
    return {"id": membro_id, "nome": m[0], "pausado": bool(m[1]), "papel": m[2],
            "fila": base.get("fila", len(leads)), "ganhos": base.get("ganhos", 0),
            "rs": base.get("rs", "R$ 0"), "conversao": base.get("conversao", "—"),
            "resp": base.get("resp", "—"), "leads": leads}


# ------------------------------------------------------------------ ATIVIDADE
def atividade(pool, conta_id: int, limite: int = 25) -> list[dict]:
    itens = []
    with pool.connection() as c:
        for emp, status, val, quando, nome in c.execute(
                """select p.empresa, p.status, """ + _VALOR_FECHADO + """, p.atualizado_em,
                          coalesce(nullif(m.nome,''), m.email, '—')
                     from prospeccao p left join membros m on m.id=p.vendedor_id
                    where p.conta_id=%s and """ + _ENCERRADO_P + """
                    order by p.atualizado_em desc limit 15""", (conta_id,)).fetchall():
            # com fase, "ganhou" é tudo que encerrou e não foi perdido — inclusive
            # uma etapa de pós-venda, que é fechamento com o evento já entregue
            g = status != "perdido"
            txt = f"{_primeiro(nome)} {'ganhou' if g else 'perdeu'} — {emp or 'lead'}"
            if g and val:
                txt += f" · {_reais(val)}"
            itens.append({"tipo": "ganho" if g else "perdido", "quando": quando, "txt": txt})
        for titulo, quando, nome in c.execute(
                """select e.titulo, e.criado_em, coalesce(nullif(m.nome,''), m.email, '—')
                     from eventos_agenda e left join membros m on m.id=e.membro_id
                    where e.conta_id=%s and e.prospeccao_id is not null and e.status='ativo'
                    order by e.criado_em desc limit 10""", (conta_id,)).fetchall():
            alvo = (titulo or "").replace("Visita — ", "")
            itens.append({"tipo": "visita", "quando": quando, "txt": f"{_primeiro(nome)} marcou visita — {alvo}"})
        for emp, quando, criador in c.execute(
                """select empresa, criado_em, criado_por from orcamentos
                    where conta_id=%s and coalesce(canal,'')='cockpit'
                    order by criado_em desc limit 10""", (conta_id,)).fetchall():
            nome = _nome_por_id(c, conta_id, criador)
            itens.append({"tipo": "prop", "quando": quando, "txt": f"{_primeiro(nome)} enviou proposta — {emp or 'cliente'}"})
    itens = [i for i in itens if i.get("quando")]
    itens.sort(key=lambda x: x["quando"], reverse=True)
    return itens[:limite]


# ------------------------------------------------------------------ AÇÕES
def reatribuir(pool, conta_id: int, lead_id: int, para_membro_id: int,
               *, por_id=None, papel: str = "dono", motivo: str = "") -> dict:
    """Move o lead pra outra pessoa, na visão de equipe do dono/gestor.

    DELEGA pro `finance.repasse`: a regra de quem pode passar e o REGISTRO da
    troca têm um dono só. Antes esta função escrevia o `update` na mão e não
    anotava nada — e um histórico que só guarda metade das trocas mente por
    omissão justamente quando alguém questiona a decisão.
    """
    from finance import repasse as _rp
    return _rp.passar(pool, conta_id, lead_id, para_membro_id,
                      por_id=por_id, papel=papel, motivo=motivo)


def pausar(pool, conta_id: int, membro_id: int, on: bool) -> dict:
    from finance import cockpit as ck
    ck.set_pausado(pool, conta_id, membro_id, on)
    return {"ok": True, "pausado": bool(on)}


def leads(pool, conta_id: int, vend: int | None = None, etapa: str = "", temp: str = "") -> list[dict]:
    """TODOS os leads abertos da equipe (fora de ganho/perdido), com o vendedor dono e
    se está com IA ou com o vendedor. Filtra por vendedor / etapa / temperatura."""
    from web.painel_prospeccao import TEMP_COR
    where = ["p.conta_id=%s", "coalesce(p.estagio,'lead')='lead'", _ABERTO_P]
    args = [conta_id]
    if vend:
        where.append("p.vendedor_id=%s")
        args.append(vend)
    if etapa:
        where.append("p.status=%s")
        args.append(etapa)
    if temp:
        where.append("p.temperatura=%s")
        args.append(temp)
    sql = ("select p.id, p.empresa, p.status, coalesce(p.temperatura,'frio'), "
           "coalesce(nullif(m.nome,''), m.email, 'Sem dono'), coalesce(cv.agente_ativo, true) "
           "from prospeccao p left join membros m on m.id=p.vendedor_id "
           "left join conversas cv on cv.prospeccao_id=p.id and cv.conta_id=p.conta_id "
           "where " + " and ".join(where) + " order by p.atualizado_em desc limit 200")
    with pool.connection() as c:
        rows = c.execute(sql, tuple(args)).fetchall()
    return [{"id": r[0], "empresa": r[1] or "Lead", "status": r[2] or "novo",
             "temp_cor": TEMP_COR.get(r[3], "#5b9bd5"), "vendedor": r[4], "ia": bool(r[5])} for r in rows]


def filtros_leads(pool, conta_id: int) -> dict:
    """Opções pros filtros da aba Leads: vendedores (com lead) + etapas do funil.

    Devolve também `rotulos` — {chave: nome que a CONTA deu} —, e é ele que a tela
    usa pra escrever etapa, no filtro e em cada linha da lista.

    POR QUE O RÓTULO PASSOU A VIR DAQUI (17/09/2026). A tela montava os nomes de uma
    tabela fixa de quatro (`_ETAPA_ROT` em web/painel_cockpit.py): novo, contatado,
    qualificado, proposta. Quem não estivesse nela virava `.title()` da chave. Na
    Prime, onde o dono renomeou as etapas, o gestor lia no app "Qualificado" onde o
    painel dizia "Agendado Visita", "Proposta" onde dizia "Negociação" e —
    literalmente — "Evento_Realizado", com sublinhado no meio da palavra. Dois
    vocabulários pro mesmo lead, e a regra 6 furada por uma constante esquecida.

    `rotulos` traz TODAS as etapas, ganho e perdido inclusive: a lista de leads
    mostra a etapa de cada um, e um lead pode estar numa que o FILTRO não oferece.
    """
    with pool.connection() as c:
        vends = c.execute(
            "select distinct m.id, coalesce(nullif(m.nome,''), m.email) from prospeccao p "
            "join membros m on m.id=p.vendedor_id where p.conta_id=%s and coalesce(p.estagio,'lead')='lead' "
            "and " + _ABERTO_P + " order by 2", (conta_id,)).fetchall()
        todas = _etapas_conta(c, conta_id)
        etapas = [e for e, _r in todas if e not in ("ganho", "perdido")]
        rotulos = {e: r for e, r in todas}
    return {"vendedores": [{"id": r[0], "nome": r[1]} for r in vends],
            "etapas": etapas, "rotulos": rotulos}


def _etapas_conta(c, conta_id: int) -> list[tuple[str, str]]:
    """(chave, rótulo) de todas as etapas da conta, na ordem do funil."""
    try:
        rows = c.execute("select chave, rotulo from funil_etapas where conta_id=%s order by ordem, id",
                         (conta_id,)).fetchall()
        if rows:
            return [(r[0], r[1] or r[0].replace("_", " ").title()) for r in rows]
    except Exception:  # noqa: BLE001
        pass
    return [("novo", "Novo"), ("contatado", "Contatado"),
            ("qualificado", "Qualificado"), ("proposta", "Proposta")]


def vendedores_para_reatribuir(pool, conta_id: int, exceto_id: int) -> list[dict]:
    with pool.connection() as c:
        rows = c.execute(
            "select id, coalesce(nullif(nome,''), email) from membros where conta_id=%s and ativo "
            "and papel in ('vendedor','gestor','dono') and id<>%s order by nome", (conta_id, exceto_id)).fetchall()
    return [{"id": r[0], "nome": r[1]} for r in rows]
