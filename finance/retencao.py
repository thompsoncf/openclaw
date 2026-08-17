"""finance/retencao.py — apagar o histórico de conversa do WhatsApp.

DUAS PORTAS, UMA MESMA FUNÇÃO DE APAGAR:

  1. O dono clica "Apagar histórico" na aba Canais, depois de desconectar.
  2. A faxina diária apaga sozinha o que passou de 30 dias desconectado.

POR QUE ISSO NÃO ACONTECE MAIS NO "DESCONECTAR"
Até o PR #404, desconectar apagava o histórico na hora. Foi removido porque
desconectar acontece sem querer — trocou de celular, o pareamento caiu, alguém
apertou "sair" no aparelho — e o histórico de conversa com os leads é o ativo
comercial da empresa. Perder isso por um pareamento que caiu é inaceitável.

O que faltava era o outro lado: quem QUER apagar não tinha como, e o que ficou
desconectado pra sempre acumulava dado pessoal sem prazo. Este módulo é esse
outro lado — apagar passa a ser um ato deliberado (o botão) ou o fim de um prazo
declarado (os 30 dias), nunca um efeito colateral de queda de conexão.

O QUE APAGA
`conversas` do canal WhatsApp, as `mensagens` delas, e `wa_contatos` (a agenda do
celular). O LEAD NÃO É TOCADO: nome, telefone, empresa, orçamentos e a posição no
funil continuam. Some a aba de conversa, não o cliente.

`wa_contatos` entra porque é agenda telefônica de terceiros — dado pessoal de
quem nunca foi cliente — e porque o próximo pareamento a reconstrói sozinha
(/webhooks/wa-qr/contatos). É o único item que volta sem custo.

`conversas_log` NÃO entra: é log técnico de turno da IA, sem texto por padrão
(depende de LOG_TEXTO_CONVERSA), com retenção própria. `wa_qr_enviadas` também
não: é cache de deduplicação de 3 dias que o serviço de QR limpa sozinho.
"""
from __future__ import annotations

import logging

_log = logging.getLogger("openclaw.retencao")

# o prazo da regra. Fica aqui, num lugar só, pra tela e faxina nunca divergirem
# do que o aviso promete ao dono.
DIAS_RETENCAO = 30


def apagar_historico_whatsapp(pool, conta_id: int) -> dict:
    """Apaga o histórico de conversa de WhatsApp de UMA conta.

    Numa transação só: ou apaga tudo, ou não apaga nada. Meio apagado é pior que
    não apagado — deixaria conversa sem mensagem na tela do vendedor.

    Devolve o que apagou, pra tela poder dizer números de verdade em vez de um
    "pronto!" que ninguém sabe se fez algo."""
    conta_id = int(conta_id)
    with pool.connection() as c:
        with c.transaction():
            # mensagens ANTES das conversas: a FK mensagens.conversa_id aponta pra
            # conversas, e a ordem inversa esbarraria nela.
            n_msg = c.execute(
                """delete from mensagens
                    where conversa_id in (select id from conversas
                                           where conta_id=%s and canal='whatsapp')""",
                (conta_id,)).rowcount or 0
            n_conv = c.execute(
                "delete from conversas where conta_id=%s and canal='whatsapp'",
                (conta_id,)).rowcount or 0
            n_cont = c.execute(
                "delete from wa_contatos where conta_id=%s", (conta_id,)).rowcount or 0
    _log.warning("retencao: conta_id=%s histórico de WhatsApp apagado — "
                 "%s mensagens, %s conversas, %s contatos",
                 conta_id, n_msg, n_conv, n_cont)
    return {"mensagens": n_msg, "conversas": n_conv, "contatos": n_cont}


def resumo_historico(pool, conta_id: int) -> dict:
    """Quanto histórico de WhatsApp existe hoje, pra o botão avisar o que vai
    apagar ANTES de apagar. Um "tem certeza?" sem número não informa nada."""
    conta_id = int(conta_id)
    with pool.connection() as c:
        r = c.execute(
            """select count(distinct cv.id), count(m.id),
                      min(m.criado_em)::date, max(m.criado_em)::date
                 from conversas cv
                 left join mensagens m on m.conversa_id = cv.id
                where cv.conta_id=%s and cv.canal='whatsapp'""", (conta_id,)).fetchone()
        n_cont = c.execute(
            "select count(*) from wa_contatos where conta_id=%s", (conta_id,)).fetchone()
    return {"conversas": int((r or [0])[0] or 0), "mensagens": int((r or [0, 0])[1] or 0),
            "de": (r[2].strftime("%d/%m/%Y") if r and r[2] else ""),
            "ate": (r[3].strftime("%d/%m/%Y") if r and r[3] else ""),
            "contatos": int((n_cont or [0])[0] or 0)}


def canais_vencidos(pool, dias: int = DIAS_RETENCAO) -> list[int]:
    """Contas cujo canal de WhatsApp está desconectado há mais de `dias`.

    TRÊS CONDIÇÕES, não uma. O prazo sozinho não basta porque este expurgo apaga
    o ativo comercial da empresa e não tem volta:

      1. `ativo = false`      — o canal está mesmo desligado agora;
      2. o prazo estourou     — `desconectado_em` é o marco da migração 165;
      3. NENHUMA mensagem depois de `desconectado_em` — prova de que o canal
         ficou escuro de verdade o tempo todo.

    A terceira existe porque o serviço de QR reconecta sozinho ao reiniciar, sem
    passar pelo painel: se ninguém abriu a tela de Canais nesse meio-tempo, o
    marco continuaria carimbado num canal que voltou a funcionar. Uma mensagem
    trafegada é prova melhor que qualquer carimbo — e ela zera a elegibilidade."""
    dias = max(1, int(dias))
    with pool.connection() as c:
        rows = c.execute(
            """select cc.conta_id
                 from canais_config cc
                where cc.canal='whatsapp'
                  and coalesce(cc.provedor,'twilio')='qr'
                  and cc.ativo = false
                  and cc.desconectado_em is not null
                  and cc.desconectado_em < now() - (%s || ' days')::interval
                  and not exists (
                        select 1 from conversas cv
                          join mensagens m on m.conversa_id = cv.id
                         where cv.conta_id = cc.conta_id and cv.canal='whatsapp'
                           and m.criado_em > cc.desconectado_em)
                order by cc.conta_id""", (str(dias),)).fetchall()
    return [int(r[0]) for r in rows]


def faxina(pool, dias: int = DIAS_RETENCAO) -> dict:
    """A passada diária: apaga o histórico de todo canal vencido.

    Tolerante a falha POR CONTA — uma conta que estoura não impede as outras de
    serem limpas, e o erro vai pro log em vez de derrubar o cron inteiro."""
    dias = max(1, int(dias))
    contas = canais_vencidos(pool, dias)
    if not contas:
        _log.info("retencao.faxina: nenhum canal desconectado há mais de %s dias", dias)
        return {"contas": 0, "mensagens": 0, "conversas": 0, "contatos": 0, "erros": 0}
    tot = {"contas": 0, "mensagens": 0, "conversas": 0, "contatos": 0, "erros": 0}
    for conta_id in contas:
        try:
            r = apagar_historico_whatsapp(pool, conta_id)
        except Exception as e:  # noqa: BLE001 — uma conta não leva as outras
            tot["erros"] += 1
            _log.warning("retencao.faxina: conta_id=%s falhou: %s: %s",
                         conta_id, type(e).__name__, e)
            continue
        tot["contas"] += 1
        for k in ("mensagens", "conversas", "contatos"):
            tot[k] += r[k]
    _log.warning("retencao.faxina: %s contas limpas (>%s dias) — %s mensagens, "
                 "%s conversas, %s contatos, %s erros",
                 tot["contas"], dias, tot["mensagens"], tot["conversas"],
                 tot["contatos"], tot["erros"])
    return tot
