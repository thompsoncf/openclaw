"""Convidados de reunião (agenda) + confirmação por link público.

Fluxo: o dono marca um evento e convida alguém (nome + contato). A gente gera um
`token` e uma página pública /convite/<token> onde o cliente Confirma / pede pra
Remarcar / Recusa — sem login. Ao responder, o dono é avisado (Telegram) e o
status aparece na agenda do portal.

Escopo multi-tenant sagrado: conta_id acompanha o convidado (vem do evento) e toda
query de portal filtra por ele. A página pública só resolve pelo token (segredo).
"""
from __future__ import annotations

import os
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
    convidado atualizado (com evento + empresa) pra avisar o dono, ou None.

    Traz `mudou`: True só quando o status REALMENTE mudou de valor. Assim, re-tocar
    o botão / reabrir o link com a MESMA resposta não avisa o dono de novo (evita a
    notificação repetida). Uma mudança de verdade (ex.: confirmado → recusado) avisa."""
    tok = (token or "").strip()
    if status not in STATUS_OK or status == "pendente":
        return None
    with pool.connection() as c:
        atual = c.execute("select status from evento_convidados where token=%s",
                          (tok,)).fetchone()
        if not atual:
            return None
        anterior = atual[0]
        c.execute(
            "update evento_convidados set status=%s, resposta=%s, respondido_em=now() "
            "where token=%s", (status, (resposta or "").strip() or None, tok))
        c.commit()
    conv = por_token(pool, tok)
    if conv is not None:
        conv["mudou"] = (anterior != status)
    return conv


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


def pos_resposta(pool, c: dict) -> None:
    """Efeitos colaterais de uma resposta de convidado — MESMO fluxo pro link web
    e pros botões do WhatsApp: avisa o dono e, se for grupo (2+) e todos já
    responderam, avisa que o grupo fechou. Best-effort (não levanta)."""
    from . import notificar
    try:
        notificar.avisar_dono_convite(
            pool, c["conta_id"], c["nome"] or "O convidado",
            c["evento"]["titulo"], ag.fmt_hora(c["evento"]),
            c["status"], c.get("resposta") or "")
        grupo = por_evento(pool, c["conta_id"], [c["evento"]["id"]]).get(
            c["evento"]["id"], [])
        if len(grupo) > 1:
            r = resumo(grupo)
            if r["fechado"]:
                notificar.avisar_dono_grupo_fechado(
                    pool, c["conta_id"], c["evento"]["titulo"],
                    ag.fmt_hora(c["evento"]), r["confirmados"], r["remarcar"],
                    r["recusados"])
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning("pos_resposta falhou", exc_info=True)


# ---- RSVP pelos botões do WhatsApp (quick reply do template) -----------------

def _digitos(s: str) -> str:
    return "".join(ch for ch in (s or "") if ch.isdigit())


def rsvp_por_texto(texto: str) -> str | None:
    """Mapeia o texto de um botão (ou resposta livre equivalente) pro status.
    None se não parecer um RSVP. Tolerante a acento/emoji/caixa."""
    import unicodedata
    t = "".join(ch for ch in unicodedata.normalize("NFD", (texto or "").strip().lower())
                if unicodedata.category(ch) != "Mn")   # remove acentos
    if not t:
        return None
    if "confirm" in t:
        return "confirmado"
    if "remarc" in t:
        return "remarcar"
    if "nao vou" in t or "nao poss" in t or "nao poder" in t or "recus" in t:
        return "recusado"
    return None


def pendentes_por_numero(pool, numero: str) -> list[dict]:
    """Convites PENDENTES cujo contato casa com o número que respondeu (compara
    pelos últimos 8 dígitos — tolera DDI/9º dígito). Só eventos ainda por vir,
    do mais próximo pro mais distante (o convidado quase sempre tem 1 aberto)."""
    alvo = _digitos(numero)[-8:]
    if len(alvo) < 8:
        return []
    with pool.connection() as c:
        rows = c.execute(
            """select cv.token, cv.contato, cv.nome, cv.conta_id,
                      e.id, e.titulo, e.inicio
                 from evento_convidados cv
                 join eventos_agenda e on e.id = cv.evento_id
                where cv.status = 'pendente' and cv.contato is not null
                  and e.inicio >= now() - interval '2 hours'
                order by e.inicio asc""").fetchall()
    out = []
    for r in rows:
        if _digitos(r[1]).endswith(alvo):
            out.append({"token": r[0], "nome": r[2], "conta_id": r[3],
                        "evento_id": r[4], "titulo": r[5], "inicio": r[6]})
    return out


def confirmacao_texto(c: dict) -> str:
    """Resposta que o Zaq manda de volta pro convidado logo após o botão."""
    ev = c["evento"]
    quando = ag.fmt_hora(ev)
    primeiro = (c.get("nome") or "").split()[0] if c.get("nome") else ""
    oi = f"{primeiro}, " if primeiro else ""
    st = c["status"]
    if st == "confirmado":
        return (f"✅ {oi}presença confirmada em *{ev['titulo']}* — {quando}! "
                f"Obrigado 🙌\n\n📆 Adicionar ao seu calendário: {link_calendario(ev)}")
    if st == "remarcar":
        return (f"🔁 Anotado{(' ' + primeiro) if primeiro else ''}! Vou avisar o "
                f"organizador que você precisa remarcar *{ev['titulo']}* ({quando}) "
                f"pra combinarem um novo horário. 👍")
    return (f"❌ Tudo bem{(', ' + primeiro) if primeiro else ''}! Anotei que você não "
            f"vai poder em *{ev['titulo']}* ({quando}). Obrigado por avisar! 🙏")


# ---- Disparo do convite pelo WhatsApp (outbound, via template aprovado) -------

def template_configurado() -> bool:
    """True quando dá pra o Zaq disparar o convite sozinho: template aprovado
    (SID na env) + credenciais Twilio presentes. O número usado é o DA EMPRESA
    (canais_config), resolvido no envio. Enquanto for False, a UI mostra só o
    envio manual (link)."""
    from . import whatsapp_twilio as wa
    return bool(os.environ.get("TWILIO_TMPL_CONVITE_SID") and wa.configurado())


def enviar_convite_whatsapp(pool, token: str) -> dict:
    """Dispara o template de convite pro número do convidado, PELO NÚMERO DA
    EMPRESA (canais_config, provedor Twilio). Funciona 'frio' (fora da janela de
    24h). As variáveis batem com o corpo aprovado no Twilio, NA ORDEM:
    {{1}} assunto/título · {{2}} data e horário. Retorno tolerante — nunca
    levanta; devolve {'ok': bool, ...}."""
    from . import whatsapp_out as wout
    c = por_token(pool, token)
    if not c:
        return {"ok": False, "erro": "convite_nao_encontrado"}
    if not (c.get("contato") or "").strip():
        return {"ok": False, "erro": "sem_numero"}
    sid = os.environ.get("TWILIO_TMPL_CONVITE_SID")
    if not sid:
        return {"ok": False, "erro": "sem_template"}
    ev = c["evento"]
    variaveis = {"1": ev["titulo"], "2": ag.fmt_hora(ev)}
    with pool.connection() as conn:
        return wout.enviar_template(conn, c["conta_id"], c["contato"], sid, variaveis)
