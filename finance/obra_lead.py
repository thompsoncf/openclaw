"""A reforma ligada ao lead que a vendeu (migração 375).

Desenho aprovado pelo dono em 25/09/2026 (docs/mockups/nicho_construcao.html,
seções 08 e 09). O funil de obra tem a coluna "Proposta" (o orçamento da
reforma) e a "Crédito em análise" (o cliente esperando o Reforma Casa Brasil);
sem o laço entre o lead e a obra, o card não andava quando o orçamento andava.

O QUE MOVE O CARD, e só pra frente (nunca puxa de volta quem já passou):
    orçamento ENVIADO   -> Proposta
    orçamento ACEITO    -> Fechado
                           (no Reforma Casa Brasil: Crédito em análise — o cliente
                            aceitou, mas o dinheiro dele ainda depende da Caixa)
    primeira parcela RECEBIDA de quem está em Crédito em análise -> Fechado
Recusado não mexe no card: o vendedor decide se foi perda e por quê (a regra 5
do funil exige o motivo, e o motivo não se adivinha).

A obra nasce do lead pelo painel (decisão 1 do dono: obra nasce no painel; o
agente não cria), com o nome do cliente.
"""
from __future__ import annotations

import logging
import re

from . import obras as _ob

_log = logging.getLogger("openclaw.obra_lead")

#: a ordem do funil de obra (finance/raio_x_perfil.py, perfil `obras`): o card só anda pra frente
_ORDEM = ("novo", "contatado", "follow_up", "qualificado", "proposta", "credito", "ganho")


def _tem_coluna(c) -> bool:
    return c.execute("""select 1 from information_schema.columns
                         where table_name='obras' and column_name='prospeccao_id'""").fetchone() is not None


def reforma_do_lead(pool, conta_id: int, lead_id: int) -> dict | None:
    """A reforma aberta a partir deste lead (a mais recente), ou None."""
    try:
        with pool.connection() as c:
            if not _tem_coluna(c):
                return None
            r = c.execute("""select id, nome from obras
                              where conta_id=%s and prospeccao_id=%s and tipo='reforma'
                              order by id desc limit 1""", (conta_id, lead_id)).fetchone()
    except Exception:  # noqa: BLE001
        return None
    return {"id": r[0], "nome": r[1]} if r else None


def abrir_reforma(pool, conta_id: int, lead_id: int) -> dict:
    """Cria a obra de reforma do lead (ou devolve a que já existe). ValueError se o
    lead não é desta conta."""
    ja = reforma_do_lead(pool, conta_id, lead_id)
    if ja:
        return ja
    with pool.connection() as c:
        r = c.execute("""select coalesce(nullif(contato, ''), empresa, 'cliente'), endereco
                           from prospeccao where id=%s and conta_id=%s""",
                      (lead_id, conta_id)).fetchone()
    if not r:
        raise ValueError("Lead não encontrado.")
    base = f"Reforma {' '.join((r[0] or 'cliente').split())}"[:80]
    nome, n = base, 1
    while True:
        try:
            o = _ob.criar_obra(pool, conta_id, nome, "reforma", endereco=(r[1] or "")[:200])
            break
        except ValueError:
            n += 1
            if n > 20:
                raise
            nome = f"{base} ({n})"
    with pool.connection() as c:
        c.execute("update obras set prospeccao_id=%s where id=%s and conta_id=%s",
                  (lead_id, o["id"], conta_id))
        c.commit()
    return {"id": o["id"], "nome": o["nome"]}


def lead_da_obra(pool, conta_id: int, obra_id: int) -> dict | None:
    """{id, nome, status, whatsapp} do lead desta obra, ou None."""
    try:
        with pool.connection() as c:
            if not _tem_coluna(c):
                return None
            r = c.execute(
                """select p.id, coalesce(nullif(p.contato, ''), p.empresa), p.status,
                          coalesce(nullif(p.whatsapp, ''), nullif(p.telefone, ''))
                     from obras o join prospeccao p on p.id = o.prospeccao_id and p.conta_id = o.conta_id
                    where o.id=%s and o.conta_id=%s""", (obra_id, conta_id)).fetchone()
    except Exception:  # noqa: BLE001
        return None
    if not r:
        return None
    return {"id": r[0], "nome": r[1], "status": r[2], "whatsapp": r[3] or ""}


def wa_numero(tel: str) -> str:
    """O número no formato do wa.me (55 + DDD + número), ou '' se não fecha."""
    d = re.sub(r"\D", "", tel or "")
    if len(d) in (10, 11):
        d = "55" + d
    return d if len(d) in (12, 13) and d.startswith("55") else ""


def _andar(pool, conta_id: int, obra_id: int, para: str, motivo: str,
           so_se_em: tuple | None = None) -> bool:
    """Move o card do lead da obra pra `para`, só pra frente. Nunca levanta."""
    try:
        with pool.connection() as c:
            if not _tem_coluna(c):
                return False
            r = c.execute(
                """select p.id, p.status from obras o
                     join prospeccao p on p.id = o.prospeccao_id and p.conta_id = o.conta_id
                    where o.id=%s and o.conta_id=%s""", (obra_id, conta_id)).fetchone()
            if not r:
                return False
            lead_id, de = r
            if so_se_em is not None and de not in so_se_em:
                return False
            if de not in _ORDEM or _ORDEM.index(de) >= _ORDEM.index(para):
                return False                      # perdido, ou já passou dali
            if not c.execute("select 1 from funil_etapas where conta_id=%s and chave=%s",
                             (conta_id, para)).fetchone():
                return False
            c.execute("""update prospeccao set status=%s, estagio='lead', atualizado_em=now()
                          where id=%s and conta_id=%s""", (para, lead_id, conta_id))
            from . import funil_regua as _fr
            _fr.registrar_movimento(c, conta_id, lead_id, de, para, motivo, None)
            c.commit()
        return True
    except Exception as e:  # noqa: BLE001 — o funil nunca segura o orçamento
        _log.info("obra_lead: o card da obra %s não andou: %s", obra_id, e)
        return False


def orcamento_enviado(pool, conta_id: int, obra_id: int) -> bool:
    return _andar(pool, conta_id, obra_id, "proposta", "orcamento_enviado")


def orcamento_aceito(pool, conta_id: int, obra_id: int, modelo_pagamento: str) -> bool:
    if modelo_pagamento == "rcb":
        return _andar(pool, conta_id, obra_id, "credito", "orcamento_aceito_rcb")
    return _andar(pool, conta_id, obra_id, "ganho", "orcamento_aceito")


def parcela_recebida(pool, conta_id: int, obra_id: int) -> bool:
    """O dinheiro do Reforma Casa Brasil caiu: quem esperava a Caixa fechou."""
    return _andar(pool, conta_id, obra_id, "ganho", "primeira_parcela", so_se_em=("credito",))
