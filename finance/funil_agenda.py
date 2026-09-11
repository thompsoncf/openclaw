"""A PONTE ENTRE O FUNIL E A AGENDA: o lead que fecha vira compromisso.

Regra 6 do FLUXO_FINAL_FUNIL_PRIME_EVENTOS_DESENVOLVEDOR_V3.

    "Ao entrar em FECHADO, o cliente deve sair do Kanban de prospecção. O cadastro,
     contrato e histórico permanecem no sistema, mas a gestão do evento passa
     automaticamente para AGENDA e RELATÓRIOS. A Agenda deve concentrar os eventos
     contratados e futuros, organizados principalmente pela data de realização."

POR QUE A PONTE PRECISA EXISTIR ANTES DE O CARD SAIR DO QUADRO
Medido na conta 34 em 11/09/2026: dos 86 compromissos da agenda, só 14 estão
ligados a um lead. A ponte entre as duas telas quase não existe — praticamente todo
evento é digitado direto na agenda, sem passar pela ficha. Tirar "Fechado" do quadro
sem isto trocaria uma coluna cheia por uma agenda vazia: os 7 leads de "Evento A
Realizar" e "Evento Realizado" sumiriam da prospecção sem aparecer em lugar nenhum.

RODA NO POLLER, E NÃO NO CAMINHO QUE MOVE O CARD
Um lead chega em "Fechado" por quatro caminhos hoje (o POST do painel, o app do
vendedor, o gatilho de contrato assinado e a aprovação da proposta), e amanhã por um
quinto. Pendurar a criação do evento em cada um seria quatro lugares pra esquecer, e
o quinto nasceria esquecido. Aqui é uma passada idempotente: quem já tem evento
ligado não ganha outro, e quem entrou por qualquer porta é alcançado no ciclo
seguinte.

O QUE ELA NÃO FAZ
Não mexe em evento que já existe — nem data, nem título, nem status. Se alguém
digitou o evento na mão antes de fechar o lead, esse é o evento; a ponte só liga a
ficha nele quando dá pra ter certeza de qual é (mesma data), e no resto se cala.
Sobrescrever o que a pessoa digitou seria pior que não ter ponte nenhuma.

NASCE DESLIGADA. `funil_etapas.agenda_ao_entrar` é false em toda etapa: sem ninguém
marcar a caixa na Régua, esta passada não encontra conta nenhuma pra percorrer.
"""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone

_log = logging.getLogger("openclaw.funil_agenda")

#: vizinho dos locks da régua (771147), do follow-up (771148) e do teto (771149)
_LOCK = 771150

#: hora em que o evento nasce quando só se sabe o DIA. A festa quase nunca é de
#: manhã, e um compromisso à meia-noite aparece no dia errado pra quem lê rápido.
_HORA_PADRAO = time(19, 0)
_UTC_BR = timedelta(hours=-3)


def etapas_que_agendam(c, conta_id: int) -> list[str]:
    """As chaves de etapa que criam compromisso ao receber um lead."""
    return [r[0] for r in c.execute(
        """select chave from funil_etapas
            where conta_id=%s and coalesce(agenda_ao_entrar, false)""",
        (conta_id,)).fetchall()]


def pendentes(c, conta_id: int, chaves: list[str]) -> list[dict]:
    """Leads nessas etapas, com data de evento, e SEM compromisso ligado à ficha.

    `evento_em is not null` é o portão: sem data não há o que agendar, e inventar
    uma data seria pôr na agenda do dono um compromisso que não existe.
    """
    if not chaves:
        return []
    linhas = c.execute(
        """select p.id, coalesce(nullif(p.contato,''), nullif(p.empresa,''), 'Cliente'),
                  p.evento_em, p.evento_tipo, p.evento_convidados, p.vendedor_id
             from prospeccao p
            where p.conta_id=%s and p.estagio='lead' and p.status = any(%s)
              and p.evento_em is not null
              and not exists (select 1 from eventos_agenda e
                               where e.conta_id = p.conta_id and e.prospeccao_id = p.id
                                 and coalesce(e.status,'ativo') <> 'cancelado')
            order by p.evento_em""", (conta_id, chaves)).fetchall()
    return [{"id": r[0], "quem": r[1], "data": r[2], "tipo": r[3],
             "convidados": r[4], "membro_id": r[5]} for r in linhas]


def _ja_existe_no_dia(c, conta_id: int, lead: dict) -> int | None:
    """O evento pode já estar na agenda, digitado à mão antes de o lead fechar.

    Procura um compromisso ATIVO e ainda SEM ficha no mesmo dia da festa, com o nome
    do cliente no título. Achando, a ponte só LIGA a ficha nele — não cria um
    segundo. Duplicar a festa na agenda do dono é o pior erro possível aqui: ele
    olharia dois compromissos e não saberia qual é o de verdade.
    """
    nome = (lead["quem"] or "").strip()
    if not nome or len(nome) < 3:
        return None
    r = c.execute(
        """select id from eventos_agenda
            where conta_id=%s and prospeccao_id is null
              and coalesce(status,'ativo') = 'ativo'
              and (inicio + interval '-3 hours')::date = %s
              and titulo ilike %s
            order by id limit 1""",
        (conta_id, lead["data"], f"%{nome}%")).fetchone()
    return r[0] if r else None


def garantir(pool, conta_id: int, lead: dict) -> str:
    """Garante o compromisso deste lead. Devolve 'ligado', 'criado' ou 'nada'."""
    from finance import agenda as ag
    with pool.connection() as c:
        achado = _ja_existe_no_dia(c, conta_id, lead)
        if achado:
            c.execute("update eventos_agenda set prospeccao_id=%s where id=%s and conta_id=%s",
                      (lead["id"], achado, conta_id))
            c.commit()
            return "ligado"
    # 19h de Brasília, convertido pra UTC como o resto do produto faz
    inicio = (datetime.combine(lead["data"], _HORA_PADRAO)
              .replace(tzinfo=timezone.utc) - _UTC_BR)
    titulo = f"{(lead['tipo'] or 'Evento').strip()} — {lead['quem']}"[:120]
    ag.criar_evento(pool, conta_id, titulo, inicio, membro_id=lead["membro_id"],
                    tipo="empresa", prospeccao_id=lead["id"],
                    tipo_evento=(lead["tipo"] or None),
                    convidados=lead["convidados"],
                    # a hora é palpite nosso: o dono ajusta, e a agenda mostra que
                    # foi sugerida em vez de fingir que alguém escolheu 19h
                    hora_sugerida=True,
                    descricao="Criado pelo funil ao fechar o lead. Confira a hora.")
    return "criado"


def rodar(pool, conta_id: int | None = None) -> dict:
    """Uma passada em todas as contas que ligaram. Chamada pelo poller.

    Best-effort por conta e por lead: uma data torta num lead não pode impedir os
    outros de irem pra agenda.
    """
    total = {"contas": 0, "criados": 0, "ligados": 0}
    with pool.connection() as lockc:
        if not lockc.execute("select pg_try_advisory_lock(%s)", (_LOCK,)).fetchone()[0]:
            return total
        try:
            with pool.connection() as c:
                contas = ([conta_id] if conta_id else
                          [r[0] for r in c.execute(
                              """select distinct conta_id from funil_etapas
                                  where coalesce(agenda_ao_entrar, false)""").fetchall()])
            for cid in contas:
                try:
                    with pool.connection() as c:
                        fila = pendentes(c, cid, etapas_que_agendam(c, cid))
                    if not fila:
                        continue
                    total["contas"] += 1
                    for lead in fila:
                        try:
                            r = garantir(pool, cid, lead)
                            if r == "criado":
                                total["criados"] += 1
                            elif r == "ligado":
                                total["ligados"] += 1
                        except Exception:  # noqa: BLE001
                            _log.warning("agenda do lead %s falhou", lead["id"], exc_info=True)
                except Exception:  # noqa: BLE001
                    _log.warning("ponte com a agenda falhou na conta %s", cid, exc_info=True)
        finally:
            lockc.execute("select pg_advisory_unlock(%s)", (_LOCK,))
    return total
