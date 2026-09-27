"""A FESTA ACONTECEU? (revisão do funil, parte 2, item 7 — aprovado em 27/09/2026)

POR QUE EXISTE. O card em Fechado ia pro Pós-festa só porque a data passou, e o
agradecimento saía mesmo se a festa tivesse sido remarcada e ninguém mudasse a data.
O card em Data segurada ficava parado pra sempre depois da festa. E a agenda não
sabe: das 10 festas da Prime que passaram em 4 meses, 1 foi marcada "aconteceu".

A CHECAGEM É UMA PERGUNTA, igual ao "veio?" da visita (`visita_rotinas`), e não uma
marcação que alguém precisa lembrar de fazer. No dia seguinte à festa, às 9h, o
vendedor do card (ou o dono, se o card é da IA) recebe "🎉 A festa da Iara aconteceu?"
no WhatsApp dos avisos e no app; às 18h, de novo, se ninguém respondeu. No quadro e
no app, o card ganha o selo com três botões:

    Aconteceu   o card vai pro Pós-festa, a festa fica "realizada" na agenda, e só
                então o agradecimento sai (`festa_rotinas.pos_festa`)
    Remarcou    o card fica, com a nota; a nova data é perguntada quando passar
    Cancelou    o card fica, com a nota; mover pro Perdido é da equipe (pede motivo)

NADA SAI PRO CLIENTE SEM O "ACONTECEU". O gatilho `festa_passou` (funil_regua)
passou a exigir a resposta. Vale pro Fechado e pra Data segurada, com ou sem sinal:
se a festa aconteceu, foi vendida. Festa cancelada na agenda não é perguntada. Só
festas daqui pra frente (`DESDE`): as antigas não são perguntadas de uma vez.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from finance import agenda as ag

_log = logging.getLogger(__name__)

_LOCK = 771186
RESPOSTAS = ("aconteceu", "remarcou", "cancelou")
#: a primeira pergunta (e a janela da equipe): 9h às 21h, Brasília
HORA_PERGUNTA = 9
HORA_SEGUNDA = 18
HORA_FIM = 21
#: o "daqui pra frente": festas antes disso não são perguntadas
DESDE = date(2026, 9, 27)
#: quantos dias a pergunta continua valendo (e o selo, no card)
JANELA_DIAS = 30


def chave_pos(c, conta_id: int) -> str | None:
    """A coluna do Pós-festa: a etapa do gatilho `festa_passou`, ativo. Sem ela, a
    conta não usa a pergunta."""
    r = c.execute("""select chave from funil_etapas where conta_id=%s and gatilho='festa_passou'
                        and coalesce(gatilho_ativo, true) order by ordem limit 1""",
                  (conta_id,)).fetchone()
    return r[0] if r else None


def sql_quem_pergunta() -> str:
    """Os status em que a pergunta vale: a venda fechada (fase fechamento, menos o
    Perdido) e a Data segurada (o gatilho da aprovação). Condição pronta pra um
    WHERE sobre `p`."""
    return ("""(p.status = 'ganho' or p.status in (
                  select fe.chave from funil_etapas fe where fe.conta_id = p.conta_id
                     and ((fe.fase = 'fechamento' and fe.chave <> 'perdido')
                          or (fe.gatilho = 'orcamento_aprovado' and fe.fase = 'venda'))))""")


def _pendentes(c, conta_id: int, hoje: date, ids: list[int] | None = None) -> list[tuple]:
    """(lead, vendedor, nome, tipo, dia, pergunta_1, pergunta_2) das festas que
    passaram, sem resposta, e não canceladas na agenda."""
    filtro = "and p.id = any(%(ids)s)" if ids is not None else ""
    return c.execute(
        f"""select p.id, p.vendedor_id,
                   coalesce(nullif(p.contato,''), nullif(p.empresa,''), ''),
                   coalesce(p.evento_tipo, ''), p.evento_em, fc.pergunta_1_em, fc.pergunta_2_em
              from prospeccao p
              left join festa_confirmacao fc on fc.prospeccao_id = p.id and fc.evento_em = p.evento_em
             where p.conta_id=%(c)s and p.estagio='lead' {filtro}
               and p.evento_em is not null and p.evento_em < %(hoje)s
               and p.evento_em >= greatest(%(desde)s, %(hoje)s - %(janela)s)
               and {sql_quem_pergunta()}
               and (fc.resposta is null)
               -- a festa CANCELADA na agenda (e nenhuma de pé no mesmo dia) não é perguntada
               and not (exists (select 1 from eventos_agenda e
                                 where e.conta_id = p.conta_id and e.prospeccao_id = p.id
                                   and coalesce(e.tipo_evento,'') <> '' and e.status = 'cancelado'
                                   and (e.inicio at time zone 'America/Sao_Paulo')::date = p.evento_em)
                        and not exists (select 1 from eventos_agenda e
                                         where e.conta_id = p.conta_id and e.prospeccao_id = p.id
                                           and coalesce(e.tipo_evento,'') <> ''
                                           and e.status in ('ativo','pre_reservado')
                                           and (e.inicio at time zone 'America/Sao_Paulo')::date = p.evento_em))
             order by p.evento_em, p.id""",
        {"c": conta_id, "hoje": hoje, "desde": DESDE, "janela": JANELA_DIAS,
         "ids": ids or []}).fetchall()


def _quem_responde(c, conta_id: int, vendedor_id: int | None) -> int | None:
    """O vendedor do card; se é a IA (o WhatsApp dela é o do dono) ou não tem, o dono
    da conta (e, sem dono, a gestão)."""
    from finance import chip_regra as _cr
    ia = _cr.membros_ia(c, conta_id)
    if vendedor_id and vendedor_id not in ia:
        return vendedor_id
    r = c.execute("""select id from membros where conta_id=%s and coalesce(ativo,true)
                        and papel in ('dono','gestor') and not (id = any(%s))
                      order by (papel='dono') desc, id limit 1""",
                  (conta_id, list(ia))).fetchone()
    return r[0] if r else None


def _pergunta(nome: str, tipo: str, dia: date) -> tuple[str, str]:
    from finance.voltar_a_chamar import primeiro_nome
    n = primeiro_nome(nome)
    n = n[:1].upper() + n[1:].lower() if n else ""
    de = f"da {n}" if n else "do cliente"
    oque = f"{tipo}, {dia:%d/%m}" if tipo else f"{dia:%d/%m}"
    return (f"🎉 A festa {de} ({oque}) aconteceu?",
            "Responda no app: ✅ Aconteceu · Remarcou · Cancelou. O agradecimento ao "
            "cliente só sai depois do ✅.")


def rodar(pool, agora: datetime | None = None) -> dict:
    """Um ciclo: a 1ª pergunta a partir das 9h do dia seguinte, a 2ª às 18h."""
    agora = agora or datetime.now(timezone.utc)
    out = {"perguntas": 0}
    loc = agora.astimezone(ag.BRT)
    if not (HORA_PERGUNTA <= loc.hour < HORA_FIM):
        return out
    hoje = loc.date()
    segunda = datetime(loc.year, loc.month, loc.day, HORA_SEGUNDA, tzinfo=ag.BRT)
    with pool.connection() as lk:
        try:
            if not lk.execute("select pg_try_advisory_lock(%s)", (_LOCK,)).fetchone()[0]:
                return out
        except Exception:  # noqa: BLE001
            return out
        try:
            try:
                with pool.connection() as c:
                    contas = [r[0] for r in c.execute(
                        """select distinct conta_id from funil_etapas
                            where gatilho='festa_passou' and coalesce(gatilho_ativo, true)""").fetchall()]
            except Exception:  # noqa: BLE001
                return out
            for conta in contas:
                try:
                    out["perguntas"] += _uma_conta(pool, conta, hoje, agora, segunda)
                except Exception as e:  # noqa: BLE001 — uma conta não segura as outras
                    _log.warning("festa_aconteceu: conta %s: %s", conta, e)
        finally:
            lk.execute("select pg_advisory_unlock(%s)", (_LOCK,))
    return out


def _uma_conta(pool, conta: int, hoje: date, agora: datetime, segunda: datetime) -> int:
    from finance import visita_rotinas as _vr
    n = 0
    with pool.connection() as c:
        linhas = _pendentes(c, conta, hoje)
        c.commit()
    for lead, vend, nome, tipo, dia, p1, p2 in linhas:
        if p1 and (p2 or agora < segunda or p1 >= segunda - timedelta(minutes=30)):
            continue
        coluna = "pergunta_2_em" if p1 else "pergunta_1_em"
        with pool.connection() as c:
            pegou = c.execute(
                f"""insert into festa_confirmacao (prospeccao_id, conta_id, evento_em, {coluna})
                    values (%(l)s, %(c)s, %(d)s, %(a)s)
                    on conflict (prospeccao_id, evento_em) do update set {coluna} = %(a)s
                     where festa_confirmacao.{coluna} is null and festa_confirmacao.resposta is null
                    returning prospeccao_id""",
                {"l": lead, "c": conta, "d": dia, "a": agora}).fetchone()
            quem = _quem_responde(c, conta, vend) if pegou else None
            c.commit()
        if not pegou:
            continue
        if quem:
            titulo, corpo = _pergunta(nome, tipo, dia)
            _vr.avisar(pool, conta, quem, titulo, corpo, f"/cockpit/lead/{lead}", origem="festa")
        n += 1
    return n


def responder(pool, conta_id: int, lead_id: int, resposta: str,
              membro_id: int | None = None, agora: datetime | None = None) -> dict:
    """A resposta da equipe. `aconteceu` leva o card pro Pós-festa e marca a festa
    como realizada na agenda; `remarcou`/`cancelou` deixam o card, com a nota."""
    from finance import funil_regua as fr
    if resposta not in RESPOSTAS:
        return {"ok": False, "erro": "Resposta inválida."}
    agora = agora or datetime.now(timezone.utc)
    hoje = agora.astimezone(ag.BRT).date()
    with pool.connection() as c:
        r = c.execute(f"""select p.status, p.evento_em from prospeccao p
                           where p.id=%s and p.conta_id=%s and p.estagio='lead'
                             and {sql_quem_pergunta()}""", (lead_id, conta_id)).fetchone()
        if not r or not r[1]:
            return {"ok": False, "erro": "Esse card não tem festa pra confirmar."}
        status, dia = r
        if dia > hoje:
            return {"ok": False, "erro": f"A festa é {dia:%d/%m}: ainda não passou."}
        c.execute("""insert into festa_confirmacao (prospeccao_id, conta_id, evento_em, resposta,
                                                    respondido_em, membro_id)
                     values (%s,%s,%s,%s,%s,%s)
                     on conflict (prospeccao_id, evento_em) do update
                        set resposta=excluded.resposta, respondido_em=excluded.respondido_em,
                            membro_id=excluded.membro_id""",
                  (lead_id, conta_id, dia, resposta, agora, membro_id))
        para = None
        if resposta == "aconteceu":
            try:
                with c.transaction():
                    c.execute("""update eventos_agenda set desfecho='realizado'
                                  where conta_id=%s and prospeccao_id=%s and coalesce(tipo_evento,'') <> ''
                                    and status in ('ativo','pre_reservado') and desfecho is null
                                    and (inicio at time zone 'America/Sao_Paulo')::date = %s""",
                              (conta_id, lead_id, dia))
            except Exception:  # noqa: BLE001 — a agenda é o enfeite; o card é o pedido
                _log.info("festa_aconteceu: agenda não marcou (lead %s)", lead_id)
            pos = chave_pos(c, conta_id)
            if pos and pos != status and c.execute(
                    """update prospeccao set status=%s, atualizado_em=now()
                        where id=%s and conta_id=%s and status=%s""",
                    (pos, lead_id, conta_id, status)).rowcount:
                fr.registrar_movimento(c, conta_id, lead_id, status, pos, "agenda", membro_id)
                para = pos
            nota = f"A festa de {dia:%d/%m} aconteceu ✓ O agradecimento ao cliente pode sair."
        elif resposta == "remarcou":
            nota = (f"A festa de {dia:%d/%m} não aconteceu: foi REMARCADA. Mude a data no card "
                    "e na agenda.")
        else:
            nota = (f"A festa de {dia:%d/%m} foi CANCELADA. Se a venda caiu, mova o card pro "
                    "Perdido com o motivo.")
        try:
            with c.transaction():
                c.execute("""insert into prospeccao_atividades (prospeccao_id, membro_id, tipo, descricao)
                             values (%s,%s,'nota',%s)""", (lead_id, membro_id, nota))
        except Exception:  # noqa: BLE001
            pass
        c.commit()
    return {"ok": True, "resposta": resposta, "para": para, "dia": dia}


def selos(c, conta_id: int, ids: list[int], agora: datetime | None = None) -> dict:
    """{lead: texto} dos cards com a festa passada e sem resposta — o selo com os
    três botões, no quadro e no app. Tolerante: sem a tabela, nenhum selo."""
    if not ids:
        return {}
    agora = agora or datetime.now(timezone.utc)
    hoje = agora.astimezone(ag.BRT).date()
    try:
        with c.transaction():
            if not chave_pos(c, conta_id):
                return {}
            return {r[0]: f"🎉 A festa de {r[4]:%d/%m} passou. Aconteceu?"
                    for r in _pendentes(c, conta_id, hoje, ids)}
    except Exception:  # noqa: BLE001
        return {}
