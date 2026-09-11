"""O TETO DE DIAS NUMA ETAPA: o prazo, as renovações e a justificativa que trava.

Regras 1 e 2 do FLUXO_FINAL_FUNIL_PRIME_EVENTOS_DESENVOLVEDOR_V3, desenhadas em
docs/mockups/fluxo_funil_prime_v3.html e aprovadas em 11/09/2026.

    "O CRM deve impedir que o vendedor use CONTACTADO como estacionamento de lead.
     O prazo inicial é de 7 dias, com duas renovações de 7 dias cada, no máximo 21.
     Cada renovação somente poderá ser liberada após o vendedor preencher
     obrigatoriamente a caixa de justificativa."

O QUE A PRODUÇÃO DIZ SOBRE ESSA REGRA (conta 34, 11/09/2026)
275 leads em Contactado — 87% do funil ativo. 75 já passados de 21 dias, 89
vencendo dentro de uma semana. E o número que valida tudo: dos 75 estourados,
ZERO tiveram mensagem do cliente nos últimos 7 dias. Nenhum está em conversa viva;
o teto não expulsa quem está sendo trabalhado.

O TETO É DA ETAPA, NÃO DO CONTACTADO
Pedido do dono no mesmo dia: "serve pra outras empresas do mesmo nicho ou outras —
sempre tem que pensar dessa forma". Então não existe aqui nenhuma regra chamada
"os 21 dias": existe `teto_dias` × `renovacoes_max` em qualquer etapa. Contactado
fica 7 × 2 por configuração; pôr teto na Proposta amanhã é preencher um campo.

O RELÓGIO É O DA ETAPA, E É ÚTIL DESDE JÁ
`funil_movimentos` grava desde 19/08/2026 com tudo desligado — é dele que sai
"desde quando este lead está nesta coluna". Lead sem movimento nenhum conta desde
que nasceu, que é o certo pros 4 leads da Prime que nunca se mexeram.

AS TRÊS TRAVAS
 1. Sem justificativa, `renovar` RECUSA. A trava é a função dizer não, não um aviso
    na tela — aviso se ignora, e o pedido do dono é que o vendedor não consiga.
 2. Acabaram as renovações, não há mais. O lead vencido não vira nada sozinho: sair
    da etapa é decisão de quem falou com o cliente (a mesma razão pela qual o D7 do
    follow-up não move pra Perdido — perder exige motivo).
 3. Todo prazo empurrado vira linha em `funil_renovacoes`. Sem a história, o
    vendedor renova toda vez que o aviso chega e o painel fica verde sem ninguém ter
    conversado com ninguém.

NASCE DESLIGADO. `teto_modo` é 'off' e nenhuma etapa nasce com `teto_dias`. Em
'observando' o motor calcula e grava com `simulado=true`, sem mandar um push
sequer — foi o ensaio que pegou o defeito do relógio da festa em 11/09.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from finance import funil_regua as fr

_log = logging.getLogger("openclaw.funil_teto")

#: vizinho dos locks da régua (771147) e do follow-up (771148)
_LOCK = 771149

#: quantos dias antes do vencimento o aviso começa a sair. O documento pede "no 6º
#: e no 7º dia de cada período de 7 dias" — que é exatamente 2 dias antes.
AVISAR_ANTES_PADRAO = 2

#: os estados do teto, do mais folgado pro mais grave
ESTADOS = ("ok", "avisar", "vencido", "esgotado")
ROTULO = {"ok": "no prazo", "avisar": "vence logo",
          "vencido": "vencido — renove ou mova", "esgotado": "teto atingido"}


def config(c, conta_id: int) -> dict:
    """A config do teto: o modo + quantos dias antes avisar.

    Vem junto da régua porque é a MESMA janela de atendimento — ter duas noções de
    "a empresa está aberta" no mesmo produto seria pior que o erro de uma.
    """
    base = fr.config(c, conta_id)
    r = c.execute("select teto_modo, teto_avisar_antes from funil_regua where conta_id=%s",
                  (conta_id,)).fetchone()
    if not r:
        return dict(base, teto_modo="off", teto_avisar_antes=AVISAR_ANTES_PADRAO)
    return dict(base, teto_modo=r[0],
                teto_avisar_antes=(r[1] if r[1] is not None else AVISAR_ANTES_PADRAO))


def etapas_com_teto(c, conta_id: int) -> dict:
    """{chave: {teto_dias, renovacoes_max, exige_justificativa, renova_sozinho_h}}
    só das etapas que TÊM teto. Etapa sem teto não entra — e o motor que percorre
    leads decide por ausência, sem precisar testar None em todo lugar."""
    linhas = c.execute(
        """select chave, teto_dias, coalesce(renovacoes_max, 0),
                  coalesce(exige_justificativa, true), renova_sozinho_h
             from funil_etapas where conta_id=%s and teto_dias is not null and teto_dias > 0""",
        (conta_id,)).fetchall()
    return {r[0]: {"teto_dias": r[1], "renovacoes_max": r[2],
                   "exige_justificativa": r[3], "renova_sozinho_h": r[4]} for r in linhas}


# ------------------------------------------------------------------ o relógio

def estado(*, desde: datetime, renovacoes: int, regra: dict, agora: datetime,
           avisar_antes: int = AVISAR_ANTES_PADRAO) -> dict:
    """Onde este lead está no teto da etapa. Puro — sem banco, pra dar pra testar.

    Devolve dias na etapa, o período em que está, quando vence, e o estado.

    POR QUE O PERÍODO SE DEDUZ DAS RENOVAÇÕES, E NÃO DO TEMPO
    A conta ingênua seria "dias // teto_dias = período". Ela mente no caso que mais
    importa: o lead que estourou e NINGUÉM renovou apareceria como se estivesse no
    3º período por decurso de prazo, quando na verdade ninguém encostou nele. O
    período é quantas vezes alguém empurrou a data — um fato com autor.
    """
    teto = int(regra["teto_dias"])
    usadas = max(0, int(renovacoes or 0))
    permitidas = max(0, int(regra.get("renovacoes_max") or 0))
    dias = max(0.0, (agora - desde).total_seconds() / 86400.0)
    vence_em = desde + timedelta(days=teto * (usadas + 1))
    faltam = (vence_em - agora).total_seconds() / 86400.0
    if faltam > avisar_antes:
        est = "ok"
    elif faltam > 0:
        est = "avisar"
    else:
        # vencido: ainda dá pra renovar? se não, o teto foi atingido de vez
        est = "vencido" if usadas < permitidas else "esgotado"
    return {"dias": dias, "periodo": usadas + 1, "periodos": permitidas + 1,
            "renovacoes": usadas, "restam": max(0, permitidas - usadas),
            "vence_em": vence_em, "faltam_dias": faltam, "estado": est,
            "total_dias": teto * (permitidas + 1)}


# ------------------------------------------------------------------ a renovação

def renovacoes_de(c, lead_id: int, etapa: str) -> int:
    """Quantas renovações este lead já usou NESTA passagem pela etapa.

    "Nesta passagem" é o detalhe que importa: o lead que saiu pra Follow-up, o
    cliente voltou a falar e ele retornou pro Contactado começa do zero. Contar
    renovação da vida inteira puniria o lead que RESSUSCITOU, que é o oposto do que
    a regra quer — ela existe contra o lead parado, não contra o lead que voltou.
    """
    r = c.execute(
        """select count(*) from funil_renovacoes fr
            where fr.prospeccao_id=%s and fr.etapa=%s
              and fr.criado_em > coalesce(
                    (select max(fm.criado_em) from funil_movimentos fm
                      where fm.prospeccao_id = fr.prospeccao_id and fm.para = fr.etapa),
                    '-infinity'::timestamptz)""", (lead_id, etapa)).fetchone()
    return int(r[0] if r else 0)


def conversa_recente(c, lead_id: int, horas: int, agora: datetime) -> bool:
    """O cliente falou nas últimas N horas? É o portão da renovação automática
    (MELHORIA 1 do mockup, que nasce desligada: `renova_sozinho_h` NULL).

    Lê `mensagens` e não "atividade registrada" pela mesma razão do resto da régua:
    "registrar atividade" teve 5 usos em toda a história da conta 34, e o vendedor
    responde pelo próprio celular — o que chega aqui pelo eco fromMe do WhatsApp.
    """
    if not horas or horas <= 0:
        return False
    r = c.execute(
        """select 1 from mensagens m join conversas cv on cv.id = m.conversa_id
            where cv.prospeccao_id=%s and m.direcao='in' and m.criado_em > %s limit 1""",
        (lead_id, agora - timedelta(hours=int(horas)))).fetchone()
    return bool(r)


def renovar(c, conta_id: int, lead_id: int, *, etapa: str, regra: dict,
            membro_id: int | None = None, justificativa: str = "",
            automatica: bool = False, agora: datetime | None = None) -> dict:
    """Empurra o prazo da etapa por mais um período. Devolve {ok, erro?, ...}.

    AS DUAS RECUSAS são o pedido do dono virando código:
      sem_renovacao       — acabaram as renovações; daqui só saindo da etapa
      justificativa       — a caixa está vazia e a etapa exige texto

    Recusar é a trava. Um aviso na tela seria contornável, e o pedido era que o
    vendedor NÃO CONSIGA liberar os dias sem dizer por quê.
    """
    agora_dado = agora            # None = o carimbo continua sendo o do banco
    agora = agora or datetime.now(timezone.utc)
    usadas = renovacoes_de(c, lead_id, etapa)
    permitidas = int(regra.get("renovacoes_max") or 0)
    if usadas >= permitidas:
        return {"ok": False, "erro": "sem_renovacao", "renovacoes": usadas}
    texto = (justificativa or "").strip()
    if not automatica and regra.get("exige_justificativa") and not texto:
        return {"ok": False, "erro": "justificativa", "renovacoes": usadas}
    desde = na_etapa_desde(c, lead_id)
    vence = (desde or agora) + timedelta(days=int(regra["teto_dias"]) * (usadas + 2))
    # QUEM RENOVA DIZ QUANDO. O `criado_em` é COMPARADO (`renovacoes_de` pergunta
    # "esta renovação é mais nova que a última entrada na etapa?"), e deixar o
    # Postgres carimbar com `now()` mistura dois relógios: o do banco e o que o
    # chamador injeta. Em produção coincidem e ninguém percebe; sob relógio
    # injetado se separam, e a contagem de renovações passa a dar zero. É o mesmo
    # defeito que `follow_up.marcar` teve em 09/09/2026 — corrigido do mesmo jeito.
    c.execute("""insert into funil_renovacoes (conta_id, prospeccao_id, etapa, ordem,
                                               membro_id, justificativa, automatica,
                                               vence_em, criado_em)
                 values (%s,%s,%s,%s,%s,%s,%s,%s, coalesce(%s, now()))""",
              (conta_id, lead_id, etapa, usadas + 1, membro_id, texto[:600], automatica,
               vence, agora_dado))
    return {"ok": True, "renovacoes": usadas + 1, "restam": permitidas - usadas - 1,
            "vence_em": vence}


def na_etapa_desde(c, lead_id: int) -> datetime | None:
    """Desde quando este lead está na etapa em que está. Do histórico; sem movimento
    nenhum, desde que o lead nasceu."""
    r = c.execute(
        """select coalesce((select max(fm.criado_em) from funil_movimentos fm
                             where fm.prospeccao_id = p.id and fm.para = p.status),
                           p.criado_em)
             from prospeccao p where p.id=%s""", (lead_id,)).fetchone()
    return r[0] if r else None


def historico(c, lead_id: int, limite: int = 20) -> list[dict]:
    """Quem renovou, quando, por quê — o que o banco nunca guardou."""
    linhas = c.execute(
        """select fr.etapa, fr.ordem, fr.justificativa, fr.automatica, fr.vence_em,
                  fr.criado_em, coalesce(nullif(mb.nome,''), mb.email, 'o sistema')
             from funil_renovacoes fr
             left join membros mb on mb.id = fr.membro_id
            where fr.prospeccao_id=%s order by fr.criado_em desc, fr.id desc limit %s""",
        (lead_id, limite)).fetchall()
    return [{"etapa": r[0], "ordem": r[1], "justificativa": r[2], "automatica": r[3],
             "vence_em": r[4], "em": r[5], "quem": r[6]} for r in linhas]


# ------------------------------------------------------------------ a passada

def leads(c, conta_id: int, agora: datetime | None = None, cfg: dict | None = None) -> list[dict]:
    """Todo lead em etapa COM teto, com o estado do prazo. Uma consulta pra conta
    inteira: a versão um-lead-por-vez seriam 275 consultas por ciclo na Prime, e o
    poller ainda tem campanha, lembrete, régua e follow-up na mesma passada."""
    agora = agora or datetime.now(timezone.utc)
    cfg = cfg or config(c, conta_id)
    regras = etapas_com_teto(c, conta_id)
    if not regras:
        return []
    linhas = c.execute(
        """select p.id, p.status, p.vendedor_id,
                  coalesce(nullif(p.contato,''), nullif(p.empresa,''), 'Lead'),
                  coalesce((select max(fm.criado_em) from funil_movimentos fm
                             where fm.prospeccao_id = p.id and fm.para = p.status),
                           p.criado_em) as desde,
                  (select count(*) from funil_renovacoes fr
                    where fr.prospeccao_id = p.id and fr.etapa = p.status
                      and fr.criado_em > coalesce(
                            (select max(fm2.criado_em) from funil_movimentos fm2
                              where fm2.prospeccao_id = p.id and fm2.para = p.status),
                            '-infinity'::timestamptz)) as renovou
             from prospeccao p
            where p.conta_id=%s and p.estagio='lead' and p.status = any(%s)""",
        (conta_id, list(regras))).fetchall()
    out = []
    for lead_id, status, vend, quem, desde, renovou in linhas:
        e = estado(desde=desde, renovacoes=renovou, regra=regras[status], agora=agora,
                   avisar_antes=cfg["teto_avisar_antes"])
        out.append(dict(e, id=lead_id, status=status, vendedor_id=vend, quem=quem,
                        desde=desde, regra=regras[status]))
    return out


def avaliar(c, conta_id: int, agora: datetime | None = None) -> dict:
    """Uma passada do teto. Devolve {avisos, simulados, renovados, pendentes}.

    `renovados` são as renovações AUTOMÁTICAS (`renova_sozinho_h`), que só existem
    em etapa que ligou essa melhoria — e nascem desligadas.
    """
    fora = {"avisos": 0, "simulados": 0, "renovados": 0, "pendentes": []}
    cfg = config(c, conta_id)
    modo = cfg["teto_modo"]
    if modo == "off":
        return fora
    agora = agora or datetime.now(timezone.utc)
    # empresa fechada não cobra ninguém — e o ensaio obedece a mesma regra, senão a
    # simulação mentiria sobre o que teria acontecido
    if not fr.dentro_da_janela(agora, cfg):
        return fora
    simulado = modo == "observando"
    out = dict(fora, pendentes=[])
    for x in leads(c, conta_id, agora, cfg):
        regra = x["regra"]
        # 1) a renovação automática vem ANTES do aviso: avisar e renovar no mesmo
        #    ciclo seria acordar o vendedor por um prazo que o sistema já empurrou.
        if (x["estado"] in ("avisar", "vencido") and regra.get("renova_sozinho_h")
                and x["restam"] > 0
                and conversa_recente(c, x["id"], regra["renova_sozinho_h"], agora)):
            if not simulado:
                r = renovar(c, conta_id, x["id"], etapa=x["status"], regra=regra,
                            automatica=True, agora=agora)
                if r.get("ok"):
                    out["renovados"] += 1
            else:
                out["renovados"] += 1
            continue
        if x["estado"] == "ok":
            continue
        # 2) o dedup é pelo FATO — o vencimento deste período. Renovou, a data muda,
        #    o fato é outro e o aviso pode sair de novo; nada muda, ninguém repete.
        cur = c.execute(
            """insert into funil_avisos (conta_id, prospeccao_id, estado, nivel, etapa,
                                         ref_em, simulado, membro_id, criado_em)
               values (%s,%s,'teto',%s,%s,%s,%s,%s,%s) on conflict do nothing""",
            (conta_id, x["id"], x["estado"], x["status"], x["vence_em"], simulado,
             x["vendedor_id"], agora))
        if cur.rowcount == 0:
            continue
        if simulado:
            out["simulados"] += 1
            continue
        out["avisos"] += 1
        out["pendentes"].append({"lead_id": x["id"], "membro_id": x["vendedor_id"],
                                 "quem": x["quem"], "estado": x["estado"],
                                 "etapa": x["status"], "faltam": x["faltam_dias"],
                                 "restam": x["restam"]})
    return out


def notificar(pool, conta_id: int, pendentes: list[dict]) -> None:
    """Um push por vendedor por passada, agrupado.

    AGRUPAR NÃO É ENFEITE: medido em 11/09/2026, o Pedro Yan tem 31 leads vencendo
    os 21 dias dentro de uma semana, a Jacqueline 28 e o Thiago 28. Um aviso por
    lead seriam 87 notificações numa manhã — que desliga a função e o aplicativo
    junto. Best-effort inteiro: o teto não pode derrubar o poller.
    """
    if not pendentes:
        return
    por_membro: dict = {}
    for p in pendentes:
        if p["membro_id"]:
            por_membro.setdefault(p["membro_id"], []).append(p)
    for membro_id, itens in por_membro.items():
        try:
            with pool.connection() as c:
                m = c.execute("select coalesce(nullif(nome,''), email), email from membros "
                              "where id=%s and conta_id=%s", (membro_id, conta_id)).fetchone()
            if not m:
                continue
            nome, email = m
            venc = [i for i in itens if i["estado"] in ("vencido", "esgotado")]
            if len(itens) == 1:
                it = itens[0]
                if it["estado"] == "esgotado":
                    titulo = f"⛔ {it['quem']} atingiu o teto de dias"
                    corpo = "Sem nova renovação — leve para negociação ou follow-up."
                elif it["estado"] == "vencido":
                    titulo = f"🔴 {it['quem']} venceu o prazo da etapa"
                    corpo = f"Renove com justificativa ou mova o lead · restam {it['restam']}"
                else:
                    titulo = f"🟡 {it['quem']} vence em {max(1, int(it['faltam']))} dia(s)"
                    corpo = "Toque para renovar ou levar adiante."
            else:
                titulo = (f"⏱️ {len(venc)} leads venceram o prazo da etapa" if venc
                          else f"⏱️ {len(itens)} leads vencem o prazo esta semana")
                corpo = " · ".join(i["quem"] for i in itens[:3])
            try:
                from finance import cockpit as _ck
                _ck.enviar_push(pool, conta_id, membro_id, titulo, corpo, "/cockpit")
            except Exception:  # noqa: BLE001
                pass
            if email and "@" in email:
                try:
                    from finance import email_sender as es
                    es.enviar_aviso(email, titulo, corpo + " Abra o Zaq para resolver.", nome=nome)
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            _log.warning("aviso de teto falhou (membro %s)", membro_id, exc_info=True)


def rodar(pool, agora: datetime | None = None) -> dict:
    """Uma passada em todas as contas que ligaram. Chamada pelo poller, junto de
    campanha, lembrete, régua e follow-up — sem cron novo no Render."""
    total = {"contas": 0, "avisos": 0, "simulados": 0, "renovados": 0}
    with pool.connection() as lockc:
        if not lockc.execute("select pg_try_advisory_lock(%s)", (_LOCK,)).fetchone()[0]:
            return total
        try:
            with pool.connection() as c:
                contas = [r[0] for r in c.execute(
                    "select conta_id from funil_regua where teto_modo <> 'off'").fetchall()]
            for conta_id in contas:
                try:
                    with pool.connection() as c:
                        r = avaliar(c, conta_id, agora)
                        c.commit()
                    total["contas"] += 1
                    for k in ("avisos", "simulados", "renovados"):
                        total[k] += r[k]
                    # a mensagem sai DEPOIS do commit: push não tem como ser desfeito
                    notificar(pool, conta_id, r["pendentes"])
                except Exception:  # noqa: BLE001
                    _log.warning("teto falhou na conta %s", conta_id, exc_info=True)
        finally:
            lockc.execute("select pg_advisory_unlock(%s)", (_LOCK,))
    return total
