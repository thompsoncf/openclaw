"""Cockpit do Vendedor — motor do app mobile (PWA) do vendedor.

Espaço enxuto e robusto SÓ pro vendedor receber e trabalhar o lead fora do painel
completo do Zaq. Login por LINK MÁGICO (sem senha): o gestor gera o link (ou o
vendedor pede pelo próprio e-mail) e o token de `cockpit_acesso` abre a sessão do
membro. Daí ele vê a fila DELE, conversa (pelo chip da empresa, via whatsapp_out),
mexe no funil e fecha (ganho/perdido).

Regras de escopo (defesa central, além da sessão):
  * o vendedor SÓ enxerga/atua em lead com prospeccao.vendedor_id = ele;
  * toda ação revalida a posse no banco (nunca confia no id que veio da tela).

Reaproveita o inbox omnichannel já existente (conversas/mensagens) e o motor de
etapas do funil — o Cockpit é uma porta mobile pra esse mesmo dado, não um silo novo.
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone

_log = logging.getLogger(__name__)

_TTL_MIN = 15                      # o link mágico vale 15 min (depois some sozinho)
_PAPEIS_OK = ("vendedor", "gestor", "dono")   # quem pode usar o Cockpit


def _agora():
    return datetime.now(timezone.utc)


# ----------------------------------------------------------------- login mágico

def gerar_token(pool, conta_id: int, membro_id: int) -> str:
    """Cria um token de acesso (1 uso, 15 min) pro membro e devolve a string."""
    token = secrets.token_urlsafe(24)
    with pool.connection() as c:
        c.execute(
            """insert into cockpit_acesso (token, conta_id, membro_id, expira_em)
               values (%s,%s,%s,%s)""",
            (token, conta_id, membro_id, _agora() + timedelta(minutes=_TTL_MIN)))
        c.commit()
    return token


def link_acesso(token: str) -> str:
    from finance.email_sender import _app_url
    return f"{_app_url()}/cockpit/entrar/{token}"


def validar_token(pool, token: str) -> dict | None:
    """Valida o token (existe e não expirou) e devolve {conta_id, membro_id, nome}.
    REUSÁVEL dentro da janela de 15 min: scanner/antivírus de e-mail costuma abrir o
    link antes do vendedor — se fosse 1 uso, queimaria e ele veria "expirado". Aqui o
    que protege é o prazo curto (15 min); `usado_em` guarda só o 1º toque (auditoria).
    Depois de expirar, não valida mais. A sessão (cookie) mantém ele logado."""
    token = (token or "").strip()
    if not token:
        return None
    with pool.connection() as c:
        r = c.execute(
            """update cockpit_acesso set usado_em=coalesce(usado_em, now())
                where token=%s and expira_em > now()
            returning conta_id, membro_id""", (token,)).fetchone()
        if not r:
            return None
        conta_id, membro_id = r[0], r[1]
        m = c.execute("select coalesce(nullif(nome,''), email), papel, ativo "
                      "from membros where id=%s and conta_id=%s",
                      (membro_id, conta_id)).fetchone()
        c.commit()
    if not m or not m[2] or (m[1] or "") not in _PAPEIS_OK:
        return None
    return {"conta_id": conta_id, "membro_id": membro_id, "nome": m[0], "papel": m[1]}


def membro_por_email(pool, email: str) -> dict | None:
    """Acha o membro ATIVO (vendedor/gestor/dono) por e-mail, pro login self-service.
    Devolve {conta_id, membro_id} do 1º match ou None. Não revela nada pra fora — o
    endpoint sempre responde 'confira seu e-mail'."""
    e = (email or "").strip().lower()
    if "@" not in e:
        return None
    with pool.connection() as c:
        r = c.execute(
            """select conta_id, id from membros
                where lower(email)=%s and ativo and papel in ('vendedor','gestor','dono')
                order by id limit 1""", (e,)).fetchone()
    return {"conta_id": r[0], "membro_id": r[1]} if r else None


# ----------------------------------------------------------------- dados / leituras

def _base_leads_sql() -> str:
    """Leads ABERTOS do vendedor (estágio 'lead', fora de ganho/perdido), com a
    última mensagem e se o agente ainda está no automático (IA) ou é a vez dele."""
    return """
        select p.id, p.empresa, p.cnpj, p.cidade, p.uf, p.temperatura, p.status,
               p.whatsapp, p.telefone,
               cv.id, coalesce(cv.agente_ativo, true), cv.ultima_msg_em,
               lm.texto, lm.autor,
               (select count(*) from mensagens mm
                 where mm.conversa_id=cv.id and mm.direcao='in'
                   and mm.criado_em > coalesce(cv.criado_em, now()) - interval '30 days') as n_in
          from prospeccao p
          left join conversas cv on cv.prospeccao_id=p.id and cv.conta_id=p.conta_id
          left join lateral (select texto, autor from mensagens
                              where conversa_id=cv.id order by criado_em desc limit 1) lm on true
         where p.conta_id=%s and p.vendedor_id=%s
           and coalesce(p.estagio,'lead')='lead'
           and p.status not in ('ganho','perdido')
         order by coalesce(cv.ultima_msg_em, p.atualizado_em) desc
         limit 100"""


def leads_do_vendedor(pool, conta_id: int, membro_id: int) -> list[dict]:
    from web.painel_prospeccao import _zap_link, TEMP_COR
    out = []
    with pool.connection() as c:
        rows = c.execute(_base_leads_sql(), (conta_id, membro_id)).fetchall()
    for r in rows:
        ia = bool(r[10])                 # agente_ativo → IA ainda atende
        ult = (r[12] or "").strip().replace("\n", " ")
        autor = r[13] or ""
        if ult:
            pref = "🤖 " if autor == "bot" else ("" if autor == "lead" else "Você: ")
            snip = (pref + ult)[:60]
        else:
            snip = "Sem mensagens ainda"
        out.append({
            "id": r[0], "empresa": r[1] or "Lead", "cnpj": r[2] or "",
            "cidade": r[3] or "", "uf": r[4] or "", "temperatura": r[5] or "frio",
            "temp_cor": TEMP_COR.get(r[5] or "frio", "#5b9bd5"),
            "status": r[6] or "novo",
            "zap": _zap_link(r[7] or r[8] or ""),
            "conversa_id": r[9], "ia": ia, "snip": snip,
        })
    return out


def _conta_membro(c, conta_id, membro_id):
    return c.execute("select coalesce(nullif(nome,''), email), email, coalesce(whatsapp,''), "
                     "coalesce(cockpit_push_ativo,true), coalesce(cockpit_pausado,false) "
                     "from membros where id=%s and conta_id=%s",
                     (membro_id, conta_id)).fetchone()


def lead_do_vendedor(pool, conta_id: int, membro_id: int, lead_id: int) -> dict | None:
    """Detalhe de UM lead do vendedor (revalida a posse). Traz a ficha, as etapas do
    funil da conta e o histórico da conversa. None se não é dele."""
    from web.painel_prospeccao import _carrega_alvo, _etapas
    alvo = _carrega_alvo(pool, conta_id, lead_id)
    if not alvo or alvo.get("vendedor_id") != membro_id:
        return None
    with pool.connection() as c:
        etapas = [e for e in _etapas(c, conta_id) if e["chave"] not in ("ganho", "perdido")]
        cv = c.execute("select id, coalesce(agente_ativo,true) from conversas "
                       "where prospeccao_id=%s and conta_id=%s order by ultima_msg_em desc limit 1",
                       (lead_id, conta_id)).fetchone()
        msgs = []
        if cv:
            rows = c.execute(
                """select direcao, autor, texto, criado_em from mensagens
                    where conversa_id=%s order by criado_em asc limit 200""", (cv[0],)).fetchall()
            for d, autor, texto, quando in rows:
                who = "ia" if autor == "bot" else ("out" if d == "out" else "in")
                msgs.append({"who": who, "texto": texto or "", "quando": quando})
    alvo["etapas"] = etapas
    alvo["conversa_id"] = cv[0] if cv else None
    alvo["ia"] = bool(cv[1]) if cv else True
    alvo["mensagens"] = msgs
    return alvo


def perfil(pool, conta_id: int, membro_id: int) -> dict:
    """Dados do vendedor + KPIs simples pro cabeçalho do perfil."""
    with pool.connection() as c:
        m = _conta_membro(c, conta_id, membro_id)
        na_fila = c.execute(
            "select count(*) from prospeccao where conta_id=%s and vendedor_id=%s "
            "and coalesce(estagio,'lead')='lead' and status not in ('ganho','perdido')",
            (conta_id, membro_id)).fetchone()[0]
        ganhos = c.execute(
            "select count(*) from prospeccao where conta_id=%s and vendedor_id=%s "
            "and status='ganho' and atualizado_em >= date_trunc('month', now())",
            (conta_id, membro_id)).fetchone()[0]
        atend = c.execute(
            "select count(distinct cv.prospeccao_id) from conversas cv "
            "where cv.conta_id=%s and cv.responsavel_membro_id=%s", (conta_id, membro_id)).fetchone()[0]
    nome, email, wa, push, pausado = (m or ("", "", "", True, False))
    return {"nome": nome or "Vendedor", "email": email or "", "whatsapp": wa or "",
            "push_ativo": bool(push), "pausado": bool(pausado),
            "na_fila": na_fila, "ganhos": ganhos, "atendidos": atend}


# ----------------------------------------------------------------- ações

def _posse(c, conta_id, membro_id, lead_id) -> bool:
    r = c.execute("select vendedor_id from prospeccao where id=%s and conta_id=%s",
                  (lead_id, conta_id)).fetchone()
    return bool(r and r[0] == membro_id)


def enviar_mensagem(pool, conta_id: int, membro_id: int, lead_id: int, texto: str) -> dict:
    """Manda uma mensagem pro lead pelo WhatsApp da empresa (dentro da janela 24h).
    Grava no inbox e ASSUME a conversa (pausa o bot). Revalida a posse."""
    from finance import whatsapp_out
    from web.painel_prospeccao import _add_msg, _conversa_id
    texto = (texto or "").strip()
    if not texto:
        return {"ok": False, "erro": "vazio"}
    with pool.connection() as c:
        if not _posse(c, conta_id, membro_id, lead_id):
            return {"ok": False, "erro": "escopo"}
        p = c.execute("select whatsapp, telefone from prospeccao where id=%s and conta_id=%s",
                      (lead_id, conta_id)).fetchone()
        numero = (p[0] or p[1] or "") if p else ""
        if not numero:
            return {"ok": False, "erro": "Lead sem número de WhatsApp."}
        res = whatsapp_out.enviar(c, conta_id, numero, texto)
        if not res.get("ok"):
            erros = {"nao_configurado": "WhatsApp não conectado (credencial no Render).",
                     "sem_numero_empresa": "Configure o WhatsApp da empresa na aba Canais.",
                     "numero_invalido": "Número do lead inválido."}
            return {"ok": False, "erro": erros.get(res.get("erro"),
                    "Não consegui enviar (a janela de 24h pode ter fechado).")}
        conv = _conversa_id(c, conta_id, lead_id, "whatsapp")
        _add_msg(c, conv, "whatsapp", "out", "humano", texto, membro_id, res.get("sid"))
        c.execute("update conversas set status='pendente', agente_ativo=false where id=%s", (conv,))
        c.commit()
    return {"ok": True}


def assumir(pool, conta_id: int, membro_id: int, lead_id: int) -> dict:
    """Tira a conversa do automático (o vendedor passa a responder). Revalida posse."""
    from web.painel_prospeccao import _conversa_id
    with pool.connection() as c:
        if not _posse(c, conta_id, membro_id, lead_id):
            return {"ok": False, "erro": "escopo"}
        conv = _conversa_id(c, conta_id, lead_id, "whatsapp")
        c.execute("update conversas set agente_ativo=false, status='pendente', "
                  "responsavel_membro_id=%s where id=%s", (membro_id, conv))
        c.commit()
    return {"ok": True}


def mudar_etapa(pool, conta_id: int, membro_id: int, lead_id: int, chave: str) -> dict:
    """Move o lead pra uma etapa do funil (não deixa cair em ganho/perdido por aqui —
    isso é o botão de fechar). Revalida posse e que a etapa é da conta."""
    chave = (chave or "").strip()
    if chave in ("ganho", "perdido"):
        return {"ok": False, "erro": "use_fechar"}
    with pool.connection() as c:
        if not _posse(c, conta_id, membro_id, lead_id):
            return {"ok": False, "erro": "escopo"}
        ok = c.execute("select 1 from funil_etapas where conta_id=%s and chave=%s",
                       (conta_id, chave)).fetchone()
        if not ok:
            return {"ok": False, "erro": "etapa_invalida"}
        c.execute("update prospeccao set status=%s, atualizado_em=now() "
                  "where id=%s and conta_id=%s", (chave, lead_id, conta_id))
        c.commit()
    return {"ok": True}


def fechar(pool, conta_id: int, membro_id: int, lead_id: int, tipo: str, motivo: str = "") -> dict:
    """Fecha o lead: 'ganho' ou 'perdido'. Em perdido, grava o motivo em obs (timeline).
    Revalida posse."""
    if tipo not in ("ganho", "perdido"):
        return {"ok": False, "erro": "tipo"}
    with pool.connection() as c:
        if not _posse(c, conta_id, membro_id, lead_id):
            return {"ok": False, "erro": "escopo"}
        c.execute("update prospeccao set status=%s, atualizado_em=now() "
                  "where id=%s and conta_id=%s", (tipo, lead_id, conta_id))
        try:
            c.execute("""insert into prospeccao_atividades (prospeccao_id, membro_id, tipo, resultado, descricao)
                         values (%s,%s,'nota',%s,%s)""",
                      (lead_id, membro_id, "fechado" if tipo == "ganho" else "sem_interesse",
                       ("Ganho 🎉" if tipo == "ganho" else f"Perdido — {motivo or 'sem motivo'}")[:400]))
        except Exception:  # noqa: BLE001 — timeline é best-effort
            pass
        c.commit()
    return {"ok": True}


def devolver_ia(pool, conta_id: int, membro_id: int, lead_id: int) -> dict:
    """Volta a conversa pro automático (reativa o agente). Revalida posse."""
    from web.painel_prospeccao import _conversa_id
    with pool.connection() as c:
        if not _posse(c, conta_id, membro_id, lead_id):
            return {"ok": False, "erro": "escopo"}
        conv = _conversa_id(c, conta_id, lead_id, "whatsapp")
        c.execute("update conversas set agente_ativo=true, status='aberta' where id=%s", (conv,))
        c.commit()
    return {"ok": True}


def set_push(pool, conta_id: int, membro_id: int, on: bool) -> None:
    with pool.connection() as c:
        c.execute("update membros set cockpit_push_ativo=%s where id=%s and conta_id=%s",
                  (bool(on), membro_id, conta_id))
        c.commit()


# ----------------------------------------------------------------- web push (assinaturas)

def salvar_assinatura(pool, conta_id: int, membro_id: int, sub: dict) -> bool:
    """Guarda (ou atualiza) a assinatura de push do navegador do vendedor."""
    endpoint = (sub or {}).get("endpoint")
    keys = (sub or {}).get("keys") or {}
    p256dh, auth = keys.get("p256dh"), keys.get("auth")
    if not (endpoint and p256dh and auth):
        return False
    with pool.connection() as c:
        c.execute(
            """insert into push_assinaturas (conta_id, membro_id, endpoint, p256dh, auth)
               values (%s,%s,%s,%s,%s)
               on conflict (endpoint) do update
                 set conta_id=excluded.conta_id, membro_id=excluded.membro_id,
                     p256dh=excluded.p256dh, auth=excluded.auth""",
            (conta_id, membro_id, endpoint, p256dh, auth))
        c.commit()
    return True


def remover_assinatura(pool, endpoint: str) -> None:
    if not endpoint:
        return
    with pool.connection() as c:
        c.execute("delete from push_assinaturas where endpoint=%s", (endpoint,))
        c.commit()


def enviar_push(pool, conta_id: int, membro_id: int, titulo: str, corpo: str, url: str = "/cockpit") -> int:
    """Dispara push pra TODAS as assinaturas ativas do vendedor (se ele deixou o push
    ligado). Best-effort: nunca levanta; apaga assinatura morta (404/410). Devolve
    quantas foram entregues. Sem chaves VAPID no ambiente, não faz nada."""
    try:
        from finance import webpush
        if not webpush.configurado():
            return 0
        with pool.connection() as c:
            push_on = c.execute("select coalesce(cockpit_push_ativo,true) from membros "
                                "where id=%s and conta_id=%s", (membro_id, conta_id)).fetchone()
            if not push_on or not push_on[0]:
                return 0
            subs = c.execute("select endpoint, p256dh, auth from push_assinaturas "
                             "where membro_id=%s and conta_id=%s", (membro_id, conta_id)).fetchall()
    except Exception as e:  # noqa: BLE001
        _log.info("push: leitura falhou (ok): %s", e)
        return 0
    enviados = 0
    dados = {"title": titulo, "body": corpo, "url": url}
    for endpoint, p256dh, auth in subs:
        sub = {"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}}
        try:
            from finance import webpush
            if webpush.enviar(sub, dados):
                enviados += 1
        except webpush.PushExpirado:
            remover_assinatura(pool, endpoint)
        except Exception as e:  # noqa: BLE001
            _log.info("push: envio falhou (ok): %s", e)
    return enviados


def set_pausado(pool, conta_id: int, membro_id: int, on: bool) -> None:
    with pool.connection() as c:
        c.execute("update membros set cockpit_pausado=%s where id=%s and conta_id=%s",
                  (bool(on), membro_id, conta_id))
        c.commit()
