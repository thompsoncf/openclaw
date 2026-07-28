"""Convidados de reunião (agenda) + confirmação por link público.

Fluxo: o dono marca um evento e convida alguém (nome + contato). A gente gera um
`token` e uma página pública /convite/<token> onde o cliente Confirma / pede pra
Remarcar / Recusa — sem login. Ao responder, o dono é avisado (Telegram) e o
status aparece na agenda do portal.

Escopo multi-tenant sagrado: conta_id acompanha o convidado (vem do evento) e toda
query de portal filtra por ele. A página pública só resolve pelo token (segredo).
"""
from __future__ import annotations

import secrets

from . import agenda as ag

STATUS_ROT = {"pendente": "Aguardando", "confirmado": "Confirmado",
              "remarcar": "Quer remarcar", "recusado": "Recusou"}
STATUS_OK = set(STATUS_ROT)


def criar_convidado(pool, conta_id: int, evento_id: int, nome: str | None,
                    contato: str | None) -> dict:
    """Cria o convidado do evento com um token único. Devolve o registro."""
    token = secrets.token_urlsafe(16)
    with pool.connection() as c:
        row = c.execute(
            """insert into evento_convidados (evento_id, conta_id, nome, contato, token)
               values (%s,%s,%s,%s,%s)
               returning id, nome, contato, token, status""",
            (evento_id, conta_id, (nome or "").strip() or None,
             (contato or "").strip() or None, token)).fetchone()
        c.commit()
    return {"id": row[0], "nome": row[1], "contato": row[2], "token": row[3],
            "status": row[4], "evento_id": evento_id, "conta_id": conta_id}


def por_token(pool, token: str) -> dict | None:
    """Tudo que a página pública de confirmação precisa: convidado + evento + a
    empresa (nome). None se o token não existe."""
    token = (token or "").strip()
    if not token:
        return None
    with pool.connection() as c:
        r = c.execute(
            """select cv.id, cv.nome, cv.contato, cv.status, cv.resposta, cv.token,
                      cv.conta_id, e.id, e.titulo, e.inicio, e.fim, e.local, e.tipo,
                      co.nome
                 from evento_convidados cv
                 join eventos_agenda e on e.id = cv.evento_id
                 join contas co on co.id = cv.conta_id
                where cv.token = %s""", (token,)).fetchone()
    if not r:
        return None
    ev = {"id": r[7], "titulo": r[8], "inicio": r[9], "fim": r[10],
          "local": r[11], "tipo": r[12] or "pessoal"}
    return {"id": r[0], "nome": r[1], "contato": r[2], "status": r[3],
            "resposta": r[4], "token": r[5], "conta_id": r[6],
            "evento": ev, "empresa": r[13]}


def responder(pool, token: str, status: str, resposta: str | None = None) -> dict | None:
    """Registra a resposta do convidado (confirmado/remarcar/recusado). Devolve o
    convidado atualizado (com evento + empresa) pra avisar o dono, ou None."""
    if status not in STATUS_OK or status == "pendente":
        return None
    with pool.connection() as c:
        cur = c.execute(
            "update evento_convidados set status=%s, resposta=%s, respondido_em=now() "
            "where token=%s",
            (status, (resposta or "").strip() or None, (token or "").strip()))
        c.commit()
        if cur.rowcount == 0:
            return None
    return por_token(pool, token)


def por_evento(pool, conta_id: int, evento_ids: list[int]) -> dict[int, list[dict]]:
    """{evento_id: [convidados]} pros eventos dados (pra pintar status na agenda)."""
    if not evento_ids:
        return {}
    with pool.connection() as c:
        rows = c.execute(
            "select evento_id, id, nome, contato, status, token from evento_convidados "
            "where conta_id=%s and evento_id = any(%s) order by id",
            (conta_id, list(evento_ids))).fetchall()
    out: dict[int, list[dict]] = {}
    for r in rows:
        out.setdefault(r[0], []).append(
            {"evento_id": r[0], "id": r[1], "nome": r[2], "contato": r[3],
             "status": r[4], "status_rot": STATUS_ROT.get(r[4], "Aguardando"),
             "token": r[5]})
    return out


def resumo(convidados: list[dict]) -> dict:
    """Contagem pro 'X de N confirmaram' e pra saber se o grupo já fechou."""
    total = len(convidados)
    conf = sum(1 for g in convidados if g["status"] == "confirmado")
    rem = sum(1 for g in convidados if g["status"] == "remarcar")
    rec = sum(1 for g in convidados if g["status"] == "recusado")
    pend = total - conf - rem - rec
    return {"total": total, "confirmados": conf, "remarcar": rem, "recusados": rec,
            "respondidos": total - pend, "fechado": total > 0 and pend == 0}


def link_calendario(ev: dict) -> str:
    """Link 'adicionar ao meu calendário' pro cliente depois de confirmar."""
    return ag.link_google(ev)
