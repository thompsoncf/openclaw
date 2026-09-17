"""A venda conta quando o CONTRATO é ASSINADO — não quando alguém arrasta o card.

REGRA DO DONO, dada em 17/09/2026: "só conta como venda quando assinar contrato".

O QUE ESTAVA ERRADO. A assinatura já abria o financeiro (`contrato.assinar` chama
`vendas.fechar_orcamento` desde sempre) e o Raio-X já contava a venda pelo contrato
(`status='ganho' OR contrato assinado`). O que NINGUÉM fazia era mover o card do
funil. Medido na conta 34 nesse dia — 7 contratos assinados:

    Bianca Oliveira ....... 29/08 ... Evento Realizado   ← a única no lugar
    Claudia Carvalho ...... 02/09 ... Evento A Realizar
    Beatriz do Carmo ...... 08/09 ... Evento A Realizar
    Jacque/Costa .......... 08/09 ... Evento A Realizar
    Renata Costa .......... 09/09 ... NEGOCIAÇÃO
    Josiany Rayra ......... 14/09 ... NEGOCIAÇÃO
    Larissa Rakel ......... 15/09 ... NEGOCIAÇÃO

Três clientes com contrato assinado apareciam como "em negociação" pro vendedor e
pro dono. E a Renata Costa estava na fila de cobrança do follow-up, sendo cobrada
"há 5 dias" — cinco dias DEPOIS de ter assinado. O sistema mandava o vendedor
correr atrás de quem já tinha fechado.

A DUAS REGRAS DESTA FUNÇÃO:

1. **Nunca anda pra trás.** Lead que já está em `ganho`, ou numa etapa DEPOIS dele
   (a Prime tem "Evento A Realizar" e "Evento Realizado" depois da venda), fica
   onde está. A assinatura é o piso da venda, não o teto — arrastar de volta pra
   `ganho` quem já teve a festa seria desfazer trabalho de gente.
2. **Nunca derruba a assinatura.** É chamada depois do commit do contrato e é
   tolerante inteira: se o funil não andar, o contrato continua assinado e o
   financeiro aberto. Mesma escolha do `fechar_orcamento` logo acima dela.
"""
from __future__ import annotations

import logging

_log = logging.getLogger("openclaw.funil_ganho")

#: Fica em `funil_movimentos.motivo`, e é por ele que se conta quantas vendas o
#: sistema marcou sozinho contra quantas alguém arrastou na mão.
MOTIVO = "contrato_assinado"


def marcar_por_assinatura(pool, conta_id: int, orcamento_id: int) -> dict:
    """Move pra `ganho` o lead deste orçamento. Devolve {ok, lead_id, de, motivo}.

    `ok=False` com motivo é o caso comum e não é erro: orçamento sem lead, lead que
    já está em ganho, lead já numa etapa de pós-venda.
    """
    try:
        with pool.connection() as c:
            with c.transaction():
                r = c.execute(
                    """select p.id, p.status from prospeccao p
                        where p.conta_id=%s and p.orcamento_id=%s
                        order by p.id limit 1""",
                    (conta_id, int(orcamento_id))).fetchone()
                if not r:
                    return {"ok": False, "motivo": "sem_lead"}
                lead_id, de = r[0], r[1] or "novo"
                if de == "ganho":
                    return {"ok": False, "lead_id": lead_id, "motivo": "ja_em_ganho"}
                # A ORDEM DECIDE O QUE É "DEPOIS". `ganho` é etapa fixa e vive no fim
                # da régua; quem estiver na mesma altura ou além dela já passou da
                # venda. Sem a tabela de etapas (base antiga), o `coalesce` deixa o
                # piso em 900, que é onde o `ganho` nasce em toda conta semeada.
                alturas = dict(c.execute(
                    "select chave, ordem from funil_etapas where conta_id=%s",
                    (conta_id,)).fetchall() or [])
                piso = alturas.get("ganho", 900)
                if alturas.get(de) is not None and alturas[de] >= piso:
                    return {"ok": False, "lead_id": lead_id, "de": de,
                            "motivo": "ja_passou_da_venda"}
                c.execute(
                    """update prospeccao set status='ganho', atualizado_em=now()
                        where id=%s and conta_id=%s""", (lead_id, conta_id))
                # O RASTRO. Sem ele o card aparece em ganho e ninguém sabe por quê —
                # e é justamente este movimento que a gente vai querer contar depois:
                # venda que o sistema marcou sozinho × venda arrastada na mão.
                c.execute(
                    """insert into funil_movimentos
                         (conta_id, prospeccao_id, de, para, motivo, membro_id)
                       values (%s,%s,%s,'ganho',%s,null)""",
                    (conta_id, lead_id, de, MOTIVO))
        _log.info("funil_ganho: lead %s da conta %s foi de %s pra ganho (contrato assinado)",
                  lead_id, conta_id, de)
        return {"ok": True, "lead_id": lead_id, "de": de}
    except Exception as e:  # noqa: BLE001 — a ASSINATURA é o que não pode se perder
        _log.warning("funil_ganho: não deu pra marcar o ganho (conta=%s, orcamento=%s): %s: %s",
                     conta_id, orcamento_id, type(e).__name__, e)
        return {"ok": False, "motivo": f"{type(e).__name__}: {e}"}
