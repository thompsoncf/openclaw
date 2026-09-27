"""A IA INSISTE QUANDO O CLIENTE SOME — o follow-up do lead que a IA atende.

Decisão do dono em 27/09/2026, ao ver o funil com as três trilhas (vendedores, IA do
número, resgate): "cada um seguindo sua regra e agente acompanhando, e claro deixando
de cobrar o ZAQ SDR como se fosse vendedor".

O QUE ESTAVA ERRADO
O lead que entra pelo chip da regra por número (migração 388) é do membro IA desde o
primeiro "oi". Quando o cliente parava de responder, ninguém insistia: a IA só fala
quando o cliente fala, e o follow-up e a esteira tratavam o membro IA como vendedor —
cobravam o WhatsApp do dono (é o número do membro IA) e, no 7º dia da esteira,
fechavam o lead como "sem tratativa". Agora a esteira e o follow-up pulam o membro IA
(`chip_regra.membros_ia`), e quem acompanha é a própria IA, aqui.

A REGRA É A DE SEMPRE, a mesma dos toques do resgate (finance/resgate.py): "3 toques e
o 4º fica como perdido". A última resposta da IA é o 1º; sem resposta do cliente,
    dia 3  → um lembrete curto, com uma coisa nova (o 2º toque do resgate)
    dia 7  → a última chamada, perguntando se ainda faz sentido (o 3º)
    dia 10 → perdido, motivo "não respondeu" — pela mesma porta do perdido automático
A conta é do SILÊNCIO: `silencio_desde` guarda a última mensagem do cliente a que os
toques se referem. Se ele fala de novo, a conta recomeça do zero — e quem responde é
a IA, pela regra do chip, como sempre.

AS TRAVAS (as mesmas do resgate, pelo mesmo motivo):
  * a conversa ainda é da IA: agente ligado e não 'pendente' (se alguém da equipe
    respondeu, `chip_regra.pausar_se_humano` já tirou a IA — e ela não volta a insistir);
  * a última mensagem da conversa é da IA (nada de insistir por cima de quem falou);
  * card aberto, sem visita marcada pra frente, festa que não passou nem é já;
  * o cliente não pediu pra parar (`resgate.RE_PARAR` na última mensagem dele);
  * o chip está de pé (só LEITURA de `canais_config`, CLAUDE.md §1);
  * das 9h às 19h de Brasília, de segunda a sábado, e dentro do horário da IA da regra;
  * no máximo `TETO_DIA` por empresa por dia, com `ESPACO_MIN` entre dois.

NASCE DESLIGADA: `chip_regra.ia_insiste`, a chave "A IA insiste quando o cliente some"
no cartão da regra. Um envio por ciclo do poller, no máximo.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

_log = logging.getLogger(__name__)

_BRT = timezone(timedelta(hours=-3))

#: vizinho da trava do resgate (771180)
_LOCK = 771181

#: os dias do silêncio — os mesmos do resgate (TOQUE_2_DIAS, TOQUE_3_DIAS e
#: PERDIDO_DEPOIS_DIAS), contados da última fala da IA
LEMBRETE_DIAS = 3
ULTIMA_DIAS = 7
PERDIDO_DIAS = 10

TETO_DIA = 20
ESPACO_MIN = 15
HORA_INI, HORA_FIM = 9, 19
DIAS_SEMANA = (0, 1, 2, 3, 4, 5)

#: em `resgate_envios`: separado dos envios do resgate, que têm o teto deles
TIPO = "toque_ia"
TIPO_ERRO_TEXTO = "erro_texto_ia"
#: o número que não recebeu não é tentado de novo por tantos dias
FALHA_DIAS = 7


def _sql_candidatos() -> str:
    from finance import funil_regua as fr
    from finance import visita as vis
    return f"""
    with base as (
      select l.prospeccao_id id, l.chip_id, l.membro_id, l.toques, l.toque_em, l.silencio_desde,
             p.status, coalesce(nullif(p.contato,''), nullif(p.empresa,''), 'Cliente') quem,
             coalesce(nullif(p.whatsapp,''), nullif(p.telefone,'')) numero
        from chip_regra_leads l
        join prospeccao p on p.id = l.prospeccao_id and p.conta_id = l.conta_id
        join chip_regra r on r.conta_id = l.conta_id and r.chip_id = l.chip_id
       where l.conta_id = %(conta)s
         and r.ativa and r.ia_ligada and r.ia_insiste and r.membro_id = l.membro_id
         and p.vendedor_id = l.membro_id and p.estagio = 'lead'
         and p.status <> 'perdido' and p.status not in {fr.sql_fechadas('p')}
         and (p.evento_em is null or p.evento_em >= current_date + 3)
         and not exists (select 1 from eventos_agenda e
                          where e.conta_id = p.conta_id and e.prospeccao_id = p.id
                            and e.inicio >= now() and {vis.sql_conta('e', festa=False)})),
    cv as (
      select distinct on (cv.prospeccao_id) cv.prospeccao_id lead, cv.id, cv.chip_id,
             cv.contato_ref, cv.agente_ativo, coalesce(cv.status,'') status
        from conversas cv join base b on b.id = cv.prospeccao_id
       where cv.conta_id = %(conta)s and cv.canal = 'whatsapp'
       order by cv.prospeccao_id, cv.ultima_msg_em desc nulls last, cv.id desc),
    ult as (
      select distinct on (m.conversa_id) m.conversa_id, m.autor, m.direcao,
             coalesce(m.status,'') st, m.criado_em
        from mensagens m join cv on cv.id = m.conversa_id
       order by m.conversa_id, m.criado_em desc, m.id desc),
    uin as (
      select distinct on (m.conversa_id) m.conversa_id, m.criado_em em, coalesce(m.texto,'') texto
        from mensagens m join cv on cv.id = m.conversa_id
       where m.direcao = 'in'
       order by m.conversa_id, m.criado_em desc, m.id desc)
    select b.id, b.quem, coalesce(b.numero, cv.contato_ref), cv.id, cv.chip_id, b.status,
           b.membro_id, uin.em, uin.texto, ult.criado_em,
           case when b.silencio_desde is not distinct from uin.em then b.toques else 0 end,
           b.toque_em
      from base b
      join cv on cv.lead = b.id
      join ult on ult.conversa_id = cv.id
      join uin on uin.conversa_id = cv.id
     where cv.agente_ativo and cv.status <> 'pendente'
       and ult.autor = 'bot' and ult.direcao = 'out' and ult.st <> 'ia_fora'"""


def _so_digitos(v) -> str:
    return "".join(ch for ch in str(v or "") if ch.isdigit())


def devidos(c, conta_id: int, agora: datetime | None = None) -> list[dict]:
    """Os leads da IA em silêncio com um passo vencido: `passo` 1 (lembrete), 2 (a
    última chamada) ou 'perdido'. Na ordem do silêncio mais antigo."""
    from finance import resgate as _rg
    agora = agora or datetime.now(timezone.utc)
    try:
        with c.transaction():
            rows = c.execute(_sql_candidatos(), {"conta": conta_id}).fetchall()
    except Exception as e:  # noqa: BLE001 — banco sem a 401
        _log.info("ia_insiste.devidos: sem fila (conta=%s): %s", conta_id, e)
        return []
    out = []
    for (lid, quem, numero, conversa, chip, status, membro, ult_in, txt_in, ult_bot, toques,
         toque_em) in rows:
        if _rg.RE_PARAR.search(txt_in or ""):
            continue                      # pediu pra parar: a IA não insiste
        passo = None
        if toques == 0 and ult_bot <= agora - timedelta(days=LEMBRETE_DIAS):
            passo = 1
        elif (toques == 1 and toque_em
              and toque_em <= agora - timedelta(days=ULTIMA_DIAS - LEMBRETE_DIAS)):
            passo = 2
        elif (toques >= 2 and toque_em
              and toque_em <= agora - timedelta(days=PERDIDO_DIAS - ULTIMA_DIAS)):
            passo = "perdido"
        if passo is None:
            continue
        out.append({"id": lid, "quem": quem, "numero": _so_digitos(numero),
                    "conversa_id": conversa, "chip_id": chip, "status": status,
                    "membro_id": membro, "ult_in": ult_in, "ultimo_envio_em": ult_bot,
                    "toques": toques, "toque_em": toque_em, "passo": passo})
    out.sort(key=lambda x: x["ult_in"])
    return out


def pode_agora(agora: datetime) -> bool:
    h = agora.astimezone(_BRT)
    return h.weekday() in DIAS_SEMANA and HORA_INI <= h.hour < HORA_FIM


def _inicio_do_dia(agora: datetime) -> datetime:
    return agora.astimezone(_BRT).replace(hour=0, minute=0, second=0, microsecond=0)


def _pode_mandar(c, conta_id: int, agora: datetime) -> bool:
    n = c.execute("select count(*) from resgate_envios where conta_id=%s and tipo=%s "
                  "and criado_em >= %s", (conta_id, TIPO, _inicio_do_dia(agora))).fetchone()[0]
    if int(n or 0) >= TETO_DIA:
        return False
    u = c.execute("select max(criado_em) from resgate_envios where conta_id=%s and tipo=%s",
                  (conta_id, TIPO)).fetchone()
    return not (u and u[0] and agora - u[0] < timedelta(minutes=ESPACO_MIN))


def _travados(c, conta_id: int, agora: datetime) -> set[int]:
    """Quem não é tentado agora: a IA não escreveu nas últimas 6h, ou o envio falhou
    nos últimos `FALHA_DIAS` (sem isto o mesmo número seria o 1º a cada passada)."""
    return {r[0] for r in c.execute(
        """select prospeccao_id from resgate_envios
            where conta_id=%s and prospeccao_id is not null
              and ((tipo=%s and criado_em > %s) or (tipo=%s and not ok and criado_em > %s))""",
        (conta_id, TIPO_ERRO_TEXTO, agora - timedelta(hours=6), TIPO,
         agora - timedelta(days=FALHA_DIAS))).fetchall()}


def _sql_ainda_calado() -> str:
    """A reconferência antes de gravar: o cliente não falou desde o silêncio que a
    conta usou, e a conversa ainda é da IA."""
    return """
       and not exists (select 1 from mensagens mi where mi.conversa_id = %(conversa)s
                         and mi.direcao = 'in' and mi.criado_em > %(ult_in)s)
       and exists (select 1 from conversas cv where cv.id = %(conversa)s
                     and cv.conta_id = %(conta)s and cv.agente_ativo and coalesce(cv.status,'') <> 'pendente')"""


def _perder(pool, conta_id: int, lead: dict, agora: datetime) -> bool:
    from finance import funil_perdido as _fp
    with pool.connection() as c:
        ok = c.execute(
            """update chip_regra_leads set perdido_em=now()
                where prospeccao_id=%(lead)s and conta_id=%(conta)s
                  and silencio_desde is not distinct from %(ult_in)s and toques >= 2"""
            + _sql_ainda_calado(),
            {"lead": lead["id"], "conta": conta_id, "ult_in": lead["ult_in"],
             "conversa": lead["conversa_id"]}).rowcount
        if not ok or not _fp.fechar(c, conta_id, {"id": lead["id"], "etapa": lead["status"]}, agora):
            c.rollback()               # o card mudou, ou o cliente falou: pessoa ganha
            return False
        c.commit()
    return True


def _um_toque(pool, conta_id: int, lead: dict, agora: datetime) -> bool:
    """Escreve e manda o lembrete (passo 1) ou a última chamada (passo 2)."""
    from finance import agente as ag
    from finance import resgate as _rg
    from finance import whatsapp_out as wo
    n = int(lead["passo"])
    # o pedido do resgate: o "2º toque" (uma coisa nova) e o "3º" (ainda faz sentido?)
    texto = _rg.redigir_toque(pool, conta_id, lead, n + 1, agora)
    if not texto:
        with pool.connection() as c:
            _rg._registrar(c, conta_id, TIPO_ERRO_TEXTO, lead=lead["id"], ok=False)
            c.commit()
        return False
    with pool.connection() as c:
        # RECONFERE E REIVINDICA antes de mandar: o cliente pode ter respondido
        # enquanto a IA escrevia, e o toque não sai duas vezes se a gravação cair
        ok = c.execute(
            """update chip_regra_leads set toques=%(n)s, toque_em=now(), silencio_desde=%(ult_in)s
                where prospeccao_id=%(lead)s and conta_id=%(conta)s
                  and (case when silencio_desde is not distinct from %(ult_in)s
                            then toques else 0 end) = %(antes)s""" + _sql_ainda_calado(),
            {"n": n, "ult_in": lead["ult_in"], "lead": lead["id"], "conta": conta_id,
             "antes": n - 1, "conversa": lead["conversa_id"]}).rowcount
        if not ok:
            c.rollback()
            return False
        _rg._registrar(c, conta_id, TIPO, lead=lead["id"], membro=lead["membro_id"],
                       ref_em=lead["ult_in"], texto=texto)
        envio_id = c.execute("select max(id) from resgate_envios where prospeccao_id=%s "
                             "and tipo=%s", (lead["id"], TIPO)).fetchone()[0]
        destino = wo.preparar(c, conta_id)
        c.commit()
    res = wo.enviar_pronto(destino, lead["numero"], texto, chip_id=lead["chip_id"])
    with pool.connection() as c:
        if res.get("ok"):
            ag._add_bot_msg(c, lead["conversa_id"], "whatsapp", texto, res.get("sid"))
        else:
            c.execute("""update chip_regra_leads set toques=%s, toque_em=%s, silencio_desde=%s
                          where prospeccao_id=%s and conta_id=%s""",
                      (lead["toques"], lead["toque_em"],
                       lead["ult_in"] if lead["toques"] else None, lead["id"], conta_id))
            c.execute("update resgate_envios set ok=false, erro=%s where id=%s",
                      (str(res.get("erro") or "falhou")[:200], envio_id))
        c.commit()
    return bool(res.get("ok"))


def _uma_conta(pool, conta_id: int, agora: datetime) -> dict:
    from finance import chip_regra as _cr
    from finance import resgate as _rg
    out = {"toques": 0, "perdidos": 0}
    with pool.connection() as c:
        todos = devidos(c, conta_id, agora)
        travados = _travados(c, conta_id, agora) if todos else set()
        c.commit()
    # o perdido não manda nada pra ninguém: sai a qualquer hora, todos de uma vez
    for lead in [x for x in todos if x["passo"] == "perdido"]:
        if _perder(pool, conta_id, lead, agora):
            out["perdidos"] += 1
    if not pode_agora(agora):
        return out
    with pool.connection() as c:
        if not _pode_mandar(c, conta_id, agora):
            c.commit()
            return out
        alvo = None
        for lead in todos:
            if lead["passo"] == "perdido" or lead["id"] in travados or not lead["numero"]:
                continue
            regra = _cr.regra(c, conta_id, lead["chip_id"])
            if not _cr.ia_pode_falar(regra, agora) or not _rg._chip_de_pe(c, conta_id, lead["chip_id"]):
                continue
            alvo = lead
            break
        c.commit()
    if alvo and _um_toque(pool, conta_id, alvo, agora):
        out["toques"] += 1
    return out


def rodar(pool, agora: datetime | None = None) -> dict:
    """Um ciclo em toda empresa com alguma regra que insiste. A trava fica fora de
    transação, pelo mesmo motivo do resgate (`resgate.rodar`): o ciclo pode passar
    do `idle_in_transaction_session_timeout` enquanto a IA escreve."""
    agora = agora or datetime.now(timezone.utc)
    total = {"toques": 0, "perdidos": 0}
    with pool.connection() as lk:
        lk.commit()
        lk.autocommit = True
        try:
            try:
                if not lk.execute("select pg_try_advisory_lock(%s)", (_LOCK,)).fetchone()[0]:
                    return total
            except Exception:  # noqa: BLE001
                return total
            try:
                try:
                    with pool.connection() as c:
                        contas = [r[0] for r in c.execute(
                            """select distinct conta_id from chip_regra
                                where ativa and ia_ligada and ia_insiste""").fetchall()]
                except Exception:  # noqa: BLE001 — banco sem a 401
                    return total
                for conta_id in contas:
                    try:
                        r = _uma_conta(pool, conta_id, agora)
                        for k in total:
                            total[k] += r.get(k, 0)
                    except Exception as e:  # noqa: BLE001
                        _log.warning("ia_insiste.rodar: conta %s: %s: %s",
                                     conta_id, type(e).__name__, e)
            finally:
                lk.execute("select pg_advisory_unlock(%s)", (_LOCK,))
        finally:
            lk.autocommit = False
    return total
