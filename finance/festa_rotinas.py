"""A PROPOSTA, A DATA SEGURADA E O PÓS-FESTA — o funil novo de eventos, parte 2b
(docs/mockups/funil_novo_rotinas.html, aprovado pelo dono em 27/09/2026 "com as
recomendações"). A visita é do `visita_rotinas`; a lista de espera, do `lista_espera`.

AS TRÊS COISAS QUE O CARD NÃO CONTAVA

1. A VALIDADE DA PROPOSTA. O link do cliente dizia "válido até" a DATA DA FESTA — meses
   de validade —, enquanto a IA já falava "7 dias". Agora a conta tem um número só
   (`proposta_validade_dias`, na Régua › Rotinas de festa, o dono muda quando quiser),
   o mesmo no card e no link. Só pra proposta EMITIDA depois de o número existir
   (`validade_desde`): a que já foi pro cliente não passa a dizer "vencida" de uma vez.
   Vencer não faz nada sozinho: o vendedor vê "venceu" e decide renovar ou perguntar.

2. A DATA SEGURADA. O card na coluna mostra quanto falta pro sinal. Quando OUTRO cliente
   pede a mesma data, a reserva passa a vencer em `reserva_disputada_h` horas (48, a
   recomendação aprovada: o "direito de preferência" do setor) e quem segura é avisado.
   A reserva da IA não encolhe (os lembretes dela já contam as 72h). Venceu sem sinal:
   a agenda já liberava a data (`agenda.expirar_pre_reservas`); agora o card volta pra
   Proposta com a nota e o selo "reserva venceu", e a lista de espera chama o próximo.

3. O PÓS-FESTA. No dia seguinte à festa (o card chega na coluna pela parte 1): no lead
   do vendedor, ele recebe o texto pronto pra agradecer e pedir avaliação; no lead da
   IA, a IA manda — agradece, pede a avaliação (com o link do Google, se a conta pôs) e
   a indicação. Uma vez por festa.

Não toca conexão, chip, `canais_config` nem pareamento (CLAUDE.md §0/§1). O que escreve
em `eventos_agenda` é só o prazo da pré-reserva disputada, e só pra encurtar.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from finance import agenda as ag

_log = logging.getLogger(__name__)

#: vizinho das travas das rotinas da visita (771173)
_LOCK = 771174

HORAS_CLIENTE = (9, 20)
HORAS_EQUIPE = (8, 21)
#: o selo "reserva venceu" fica no card por este tempo
VENCEU_DIAS = 7
#: a contagem em horas aparece quando falta menos que isto
CONTAGEM_H = 72
_SEMANA = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")


# ------------------------------------------------------------------ config

def config(c, conta_id: int) -> dict:
    """Os números da conta (Funil › Régua › Rotinas de festa). Tolerante: sem a 421,
    nada disto existe e tudo segue como antes."""
    base = {"proposta_validade_dias": None, "validade_desde": None,
            "reserva_disputada_h": None, "pos_festa": False, "pos_festa_desde": None,
            "avaliacao_link": ""}
    try:
        with c.transaction():
            r = c.execute("""select proposta_validade_dias, validade_desde, reserva_disputada_h,
                                    pos_festa, pos_festa_desde, coalesce(avaliacao_link, '')
                               from visita_rotinas_config where conta_id=%s""",
                          (conta_id,)).fetchone()
    except Exception:  # noqa: BLE001
        return base
    if not r:
        return base
    return {"proposta_validade_dias": r[0], "validade_desde": r[1], "reserva_disputada_h": r[2],
            "pos_festa": bool(r[3]), "pos_festa_desde": r[4], "avaliacao_link": r[5]}


def link_valido(link: str) -> str:
    """Só http(s), e sem espaço: é o que vai dentro da mensagem ao cliente."""
    link = (link or "").strip()
    if not link:
        return ""
    if not link.lower().startswith(("http://", "https://")):
        link = "https://" + link
    return link if " " not in link and "." in link else ""


def salvar_config(c, conta_id: int, *, validade_dias, disputada_h, pos_festa: bool,
                  avaliacao_link: str) -> str | None:
    """Grava. Devolve o erro, ou None. `validade_desde` e `pos_festa_desde` andam só
    quando o número/chave passa a existir — é o marco do "daqui pra frente"."""
    def _int(v, lo, hi):
        try:
            v = int(str(v).strip())
        except (TypeError, ValueError):
            return None
        return v if lo <= v <= hi else None
    dias = _int(validade_dias, 1, 90) if str(validade_dias or "").strip() else None
    if str(validade_dias or "").strip() and dias is None:
        return "A validade da proposta vai de 1 a 90 dias (ou em branco, pra não ter)."
    horas = _int(disputada_h, 0, 240) if str(disputada_h or "").strip() else None
    if str(disputada_h or "").strip() and horas is None:
        return "A reserva disputada vai de 0 a 240 horas (0 ou em branco: não encurta)."
    link = link_valido(avaliacao_link)
    if (avaliacao_link or "").strip() and not link:
        return "O link de avaliação não parece um endereço (ex.: https://g.page/r/...)."
    c.execute("""insert into visita_rotinas_config (conta_id) values (%s)
                 on conflict (conta_id) do nothing""", (conta_id,))
    c.execute("""update visita_rotinas_config
                    set validade_desde = case when %(dias)s::int is not null
                                               and proposta_validade_dias is null
                                              then now() else validade_desde end,
                        proposta_validade_dias = %(dias)s,
                        reserva_disputada_h = nullif(%(horas)s::int, 0),
                        pos_festa_desde = case when %(pf)s and not pos_festa then now()
                                               else pos_festa_desde end,
                        pos_festa = %(pf)s, avaliacao_link = nullif(%(link)s, ''),
                        atualizado_em = now()
                  where conta_id = %(conta)s""",
              {"dias": dias, "horas": horas, "pf": bool(pos_festa), "link": link,
               "conta": conta_id})
    return None


# ------------------------------------------------------------------ 1. a validade

def validade(cfg: dict, criado: date | None, dia_evento: date | None) -> date | None:
    """Até quando a proposta vale pela regra da conta, ou None quando a regra não se
    aplica a ela (conta sem número, ou proposta emitida antes de o número existir) —
    aí quem chama segue o que era."""
    dias = cfg.get("proposta_validade_dias")
    desde = cfg.get("validade_desde")
    if not dias or not desde or not criado:
        return None
    if criado < desde.astimezone(ag.BRT).date():
        return None
    ate = criado + timedelta(days=int(dias))
    return min(ate, dia_evento) if dia_evento else ate


def validade_do_orcamento(pool, conta_id: int, criado: date | None,
                          dia_evento: date | None) -> date | None:
    """O mesmo, pra quem só tem o pool (o link do cliente, web/proposta.py)."""
    try:
        with pool.connection() as c:
            return validade(config(c, conta_id), criado, dia_evento)
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------ 2. a data segurada

def _chave_segurada(c, conta_id: int) -> str | None:
    r = c.execute("""select chave from funil_etapas where conta_id=%s
                      and gatilho='orcamento_aprovado' and fase='venda' limit 1""",
                  (conta_id,)).fetchone()
    return r[0] if r else None


def _avisar(pool, conta_id: int, membro_id, titulo: str, corpo: str, url: str) -> bool:
    from finance import visita_rotinas as _vr
    return _vr.avisar(pool, conta_id, membro_id, titulo, corpo, url, origem="festa")


def disputas(pool, conta_id: int, cfg: dict, agora: datetime) -> int:
    """OUTRO CLIENTE PEDIU A DATA SEGURADA: a reserva encolhe pra `reserva_disputada_h`
    (só encolhe, nunca estica) e quem segura é avisado. Uma vez por reserva
    (`data_disputas`). A reserva da IA não encolhe — os lembretes dela contam as 72h —,
    mas o aviso sai igual."""
    horas = cfg.get("reserva_disputada_h")
    from finance.lista_espera import ABERTOS
    n = 0
    with pool.connection() as c:
        # SÓ O CARD QUE ESTÁ EM DATA SEGURADA. A venda fechada cuja festa ficou como
        # pré-reserva na agenda (medido na Prime em 27/09: uma, vencendo em 30/09) não
        # é reserva esperando sinal — encolher o prazo dela liberaria a data de quem já
        # comprou.
        chave = _chave_segurada(c, conta_id)
        if not chave:
            return 0
        rows = c.execute(
            """select e.id, e.prospeccao_id, e.pre_reserva_ate,
                      (e.inicio at time zone 'America/Sao_Paulo')::date,
                      p.vendedor_id, coalesce(nullif(p.contato,''), nullif(p.empresa,''), ''),
                      exists (select 1 from ia_orcamentos io
                               where io.conta_id = e.conta_id and io.orcamento_id = p.orcamento_id),
                      (select min(o.id) from prospeccao o
                        where o.conta_id = e.conta_id and o.id <> e.prospeccao_id
                          and o.estagio = 'lead' and o.status = any(%s)
                          and o.evento_em = (e.inicio at time zone 'America/Sao_Paulo')::date)
                 from eventos_agenda e
                 join prospeccao p on p.id = e.prospeccao_id and p.conta_id = e.conta_id
                where e.conta_id=%s and e.status='pre_reservado' and e.pre_reserva_ate > %s
                  and coalesce(e.tipo_evento, '') <> '' and p.status = %s
                  and not exists (select 1 from data_disputas d where d.evento_id = e.id)""",
            (list(ABERTOS), conta_id, agora, chave)).fetchall()
        c.commit()
    for ev, lead, ate, dia, vend, nome, da_ia, outro in rows:
        if not outro:
            continue
        novo = ate
        if horas and not da_ia and ate - agora > timedelta(hours=int(horas)):
            novo = agora + timedelta(hours=int(horas))
        with pool.connection() as c:
            pegou = c.execute("""insert into data_disputas (evento_id, conta_id, prospeccao_id,
                                                            quem_pediu, prazo_antes, prazo_novo)
                                 values (%s,%s,%s,%s,%s,%s) on conflict do nothing
                                 returning evento_id""",
                              (ev, conta_id, lead, outro, ate, novo)).fetchone()
            if pegou and novo != ate:
                c.execute("""update eventos_agenda set pre_reserva_ate=%s
                              where id=%s and conta_id=%s and status='pre_reservado'
                                and pre_reserva_ate > %s""", (novo, ev, conta_id, novo))
            c.commit()
        if not pegou:
            continue
        n += 1
        quando = f"{_SEMANA[dia.weekday()]} {dia:%d/%m}"
        vence = novo.astimezone(ag.BRT)
        corpo = (f"A reserva {('de ' + nome) if nome else ''} vence "
                 f"{vence:%d/%m} às {vence.hour}h"
                 + (f" (passou pra {int(horas)}h por causa da disputa)" if novo != ate else "")
                 + ". Vale lembrar do sinal.")
        _avisar(pool, conta_id, vend, f"⚠️ Outro cliente pediu {quando}", corpo,
                f"/cockpit/lead/{lead}")
    return n


def reservas_vencidas(pool, conta_id: int, agora: datetime) -> int:
    """A RESERVA VENCEU SEM SINAL: a agenda já cancelou a pré-reserva (`expirar_pre_
    reservas`); o card que estava em Data segurada volta pra Proposta, com a nota. O
    movimento sai como 'agenda' — mão humana na TRAVA 3 —, senão o gatilho da aprovação
    devolveria o card pra Data segurada no ciclo seguinte."""
    from finance import funil_regua as fr
    n = 0
    with pool.connection() as c:
        chave = _chave_segurada(c, conta_id)
        if not chave:
            return 0
        rows = c.execute(
            """select p.id, p.vendedor_id,
                      (select max(e.pre_reserva_ate) from eventos_agenda e
                        where e.conta_id = p.conta_id and e.prospeccao_id = p.id
                          and e.status = 'cancelado' and e.pre_reserva_ate <= %s)
                 from prospeccao p
                where p.conta_id=%s and p.status=%s and p.estagio='lead'
                  and not exists (select 1 from eventos_agenda e
                                   where e.conta_id = p.conta_id and e.prospeccao_id = p.id
                                     and coalesce(e.tipo_evento,'') <> ''
                                     and e.status in ('ativo','pre_reservado'))
                  and exists (select 1 from eventos_agenda e
                               where e.conta_id = p.conta_id and e.prospeccao_id = p.id
                                 and e.status = 'cancelado' and e.pre_reserva_ate <= %s)""",
            (agora, conta_id, chave, agora)).fetchall()
        for lead, vend, venceu in rows:
            if not c.execute("""update prospeccao set status='proposta', atualizado_em=now()
                                 where id=%s and conta_id=%s and status=%s""",
                             (lead, conta_id, chave)).rowcount:
                continue
            fr.registrar_movimento(c, conta_id, lead, chave, "proposta", "agenda")
            try:
                with c.transaction():
                    c.execute("""insert into prospeccao_atividades (prospeccao_id, tipo, descricao)
                                 values (%s,'nota',%s)""",
                              (lead, f"A reserva da data venceu em "
                                     f"{venceu.astimezone(ag.BRT):%d/%m} sem o sinal: a data "
                                     "foi liberada. Chame antes que outro feche."))
            except Exception:  # noqa: BLE001
                pass
            n += 1
        c.commit()
    return n


# ------------------------------------------------------------------ 3. o pós-festa

def texto_pos_festa(nome: str, dia_festa: date | None, hoje: date, link: str,
                    com_indicacao: bool) -> str:
    quando = "ontem" if dia_festa and (hoje - dia_festa).days == 1 else "na sua festa"
    txt = (f"Oi{', ' + nome if nome else ''}! Obrigada por celebrar com a gente {quando} 🎉 "
           "Deu tudo certo?")
    if link:
        txt += f" Se puder, deixa sua avaliação aqui, ajuda muito: {link}"
    if com_indicacao:
        txt += ("\n\nE se alguém que você conhece estiver planejando uma festa, me passa o "
                "contato que eu cuido com carinho 😊")
    return txt


def pos_festa(pool, conta_id: int, cfg: dict, agora: datetime) -> int:
    """Uma vez por festa, a partir das 9h do dia seguinte: o lead da IA recebe a
    mensagem pela conversa da IA; o do vendedor vira aviso com o texto pronto."""
    if not cfg.get("pos_festa") or not cfg.get("pos_festa_desde"):
        return 0
    loc = agora.astimezone(ag.BRT)
    hoje = loc.date()
    desde = cfg["pos_festa_desde"].astimezone(ag.BRT).date() - timedelta(days=1)
    link = cfg.get("avaliacao_link") or ""
    from finance import chip_regra as _cr
    from finance.voltar_a_chamar import primeiro_nome
    n = 0
    with pool.connection() as c:
        ia = _cr.membros_ia(c, conta_id)
        rows = c.execute(
            """select p.id, p.vendedor_id, coalesce(nullif(p.contato,''), nullif(p.empresa,''), ''),
                      p.evento_em,
                      (select cv.id from conversas cv where cv.conta_id = p.conta_id
                          and cv.prospeccao_id = p.id and cv.canal = 'whatsapp'
                          and coalesce(cv.agente_ativo, false) order by cv.id desc limit 1)
                 from prospeccao p
                where p.conta_id=%s and p.status='pos_festa' and p.estagio='lead'
                  and p.evento_em is not null and p.evento_em >= %s and p.evento_em < %s
                  and not exists (select 1 from pos_festa_envios x where x.prospeccao_id = p.id)""",
            (conta_id, desde, hoje)).fetchall()
        c.commit()
    for lead, vend, quem, dia, conv in rows:
        nome = primeiro_nome(quem)
        if vend in ia and conv:
            if not (HORAS_CLIENTE[0] <= loc.hour < HORAS_CLIENTE[1]):
                continue
            if not _reivindicar(pool, conta_id, lead, "ia"):
                continue
            texto = texto_pos_festa(nome, dia, hoje, link, com_indicacao=True)
            try:
                from finance import agente
                from finance import ia_visita as _iv
                with pool.connection() as c:
                    res = _iv._mandar(c, conta_id, conv, texto)
                    if res.get("ok"):
                        agente._add_bot_msg(c, conv, "whatsapp", texto, res.get("sid"))
                    else:
                        c.execute("delete from pos_festa_envios where prospeccao_id=%s", (lead,))
                    c.commit()
                n += bool(res.get("ok"))
            except Exception as e:  # noqa: BLE001
                _log.warning("pos_festa: envio falhou (lead %s): %s", lead, e)
            continue
        if not (HORAS_EQUIPE[0] + 1 <= loc.hour < HORAS_EQUIPE[1]):
            continue
        if not vend or not _reivindicar(pool, conta_id, lead, "vendedor"):
            continue
        sugestao = texto_pos_festa(nome, dia, hoje, link, com_indicacao=True)
        _avisar(pool, conta_id, vend, f"🎉 A festa {('de ' + nome) if nome else ''} foi "
                                      + ("ontem" if (hoje - dia).days == 1 else f"em {dia:%d/%m}"),
                "Agradeça e peça a avaliação e a indicação. Texto pronto pra copiar:\n\n" + sugestao,
                f"/cockpit/lead/{lead}")
        n += 1
    return n


def _reivindicar(pool, conta_id: int, lead: int, quem: str) -> bool:
    with pool.connection() as c:
        r = c.execute("""insert into pos_festa_envios (prospeccao_id, conta_id, quem)
                         values (%s,%s,%s) on conflict do nothing returning prospeccao_id""",
                      (lead, conta_id, quem)).fetchone()
        c.commit()
    return bool(r)


# ------------------------------------------------------------------ o relógio

def rodar(pool, agora: datetime | None = None) -> dict:
    agora = agora or datetime.now(timezone.utc)
    out = {"disputas": 0, "reservas_vencidas": 0, "pos_festa": 0}
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
                        """select conta_id from visita_rotinas_config
                            where reserva_disputada_h is not null or pos_festa
                               or proposta_validade_dias is not null
                            order by conta_id""").fetchall()]
            except Exception:  # noqa: BLE001 — banco sem a 421
                return out
            for conta_id in contas:
                try:
                    with pool.connection() as c:
                        cfg = config(c, conta_id)
                        c.commit()
                    out["reservas_vencidas"] += reservas_vencidas(pool, conta_id, agora)
                    if (HORAS_EQUIPE[0] <= agora.astimezone(ag.BRT).hour < HORAS_EQUIPE[1]):
                        out["disputas"] += disputas(pool, conta_id, cfg, agora)
                    out["pos_festa"] += pos_festa(pool, conta_id, cfg, agora)
                except Exception as e:  # noqa: BLE001
                    _log.warning("festa_rotinas.rodar: conta %s: %s", conta_id, e)
        finally:
            lk.execute("select pg_advisory_unlock(%s)", (_LOCK,))
    return out


# ------------------------------------------------------------------ o card

def selos(c, conta_id: int, cards: list[dict], agora: datetime | None = None) -> dict:
    """O selo da proposta e da data segurada, numa consulta por coisa pro quadro
    inteiro: {lead: (texto, classe)}.

        proposta       vale até 02/10 · vence amanhã · venceu 02/10 · reserva venceu
        data segurada  ⏳ 41h pro sinal · sinal até 20/10 · outro cliente pediu a data
    Tolerante: sem as tabelas ou sem a regra, nenhum selo."""
    agora = agora or datetime.now(timezone.utc)
    hoje = agora.astimezone(ag.BRT).date()
    ids = [x["id"] for x in cards]
    if not ids:
        return {}
    out: dict = {}
    try:
        with c.transaction():
            cfg = config(c, conta_id)
            chave = _chave_segurada(c, conta_id)
            # a proposta: o orçamento ligado ao card, na coluna Proposta
            if cfg.get("proposta_validade_dias"):
                for lead, criado, evento in c.execute(
                        """select p.id, (o.criado_em at time zone 'America/Sao_Paulo')::date,
                                  p.evento_em
                             from prospeccao p join orcamentos o on o.id = p.orcamento_id
                            where p.conta_id=%s and p.id = any(%s) and p.status='proposta'
                              and o.status = 'enviado'""", (conta_id, ids)).fetchall():
                    v = validade(cfg, criado, evento)
                    if not v:
                        continue
                    falta = (v - hoje).days
                    out[lead] = ((f"vale até {v:%d/%m}", "nt") if falta > 1
                                 else ("vence amanhã", "at") if falta == 1
                                 else ("vence hoje", "at") if falta == 0
                                 else (f"venceu {v:%d/%m}", "bad"))
            # a reserva que venceu: o selo por alguns dias depois de voltar
            if chave:
                for (lead,) in c.execute(
                        """select distinct m.prospeccao_id from funil_movimentos m
                            where m.conta_id=%s and m.prospeccao_id = any(%s) and m.de=%s
                              and m.para='proposta' and m.motivo='agenda'
                              and m.criado_em > %s""",
                        (conta_id, ids, chave, agora - timedelta(days=VENCEU_DIAS))).fetchall():
                    out[lead] = ("reserva venceu", "bad")
            # a data segurada: o prazo do sinal, e a disputa
            for lead, ate, disputada in c.execute(
                    """select e.prospeccao_id, min(e.pre_reserva_ate),
                              bool_or(exists (select 1 from data_disputas d where d.evento_id = e.id))
                         from eventos_agenda e
                        where e.conta_id=%s and e.prospeccao_id = any(%s)
                          and e.status='pre_reservado' and e.pre_reserva_ate > %s
                        group by e.prospeccao_id""", (conta_id, ids, agora)).fetchall():
                falta = ate - agora
                txt = (f"⏳ {max(1, int(falta.total_seconds() // 3600))}h pro sinal"
                       if falta < timedelta(hours=CONTAGEM_H)
                       else f"⏳ sinal até {ate.astimezone(ag.BRT):%d/%m}")
                if disputada:
                    txt += " · outro cliente pediu a data"
                out[lead] = (txt, "bad" if disputada else "at")
    except Exception:  # noqa: BLE001
        return {}
    return out
