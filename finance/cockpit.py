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


# ----------------------------------------------------------------- orçamento / proposta

def catalogo_servicos(pool, conta_id: int) -> list[dict]:
    """Serviços do catálogo da empresa pro vendedor montar o orçamento. Preços em
    REAIS (inteiros) — o snapshot de itens da proposta é em reais (ver web/proposta)."""
    from finance import servicos_catalogo as scat
    out = []
    for s in scat.listar(pool, conta_id):
        out.append({"id": s["id"], "nome": s["nome"], "desc": s.get("descricao", ""),
                    "setup": round((s.get("setup_centavos") or 0) / 100),
                    "mensal": round((s.get("mensal_centavos") or 0) / 100)})
    return out


def _sanear_itens(itens) -> list[dict]:
    """Snapshot seguro das linhas: nome + setup/mensal em REAIS (inteiros, ≥0)."""
    out = []
    for it in (itens or [])[:50]:
        nome = (str(it.get("nome") or "")).strip()[:120]
        if not nome:
            continue
        out.append({"nome": nome, "desc": (str(it.get("desc") or "")).strip()[:200],
                    "setup": max(0, int(it.get("setup") or 0)),
                    "mensal": max(0, int(it.get("mensal") or 0))})
    return out


# ------------------------------------------------------------------ carteira de propostas
STATUS_ORC = ("rascunho", "enviado", "negociando", "aprovada", "fechado", "perdido")
_ROT_ORC = {"rascunho": "Rascunho", "enviado": "Enviada", "negociando": "Negociando",
            "aprovada": "Aprovada", "fechado": "Fechada", "perdido": "Perdida"}


def _link_proposta(token: str) -> str:
    from finance.email_sender import _app_url
    return f"{_app_url()}/proposta/{token}" if token else ""


def _curar_tokens(c, conta_id: int) -> None:
    """Proposta sem token não tem link pro cliente — as antigas foram salvas antes do
    token existir. Mesma auto-cura que painel_servicos.lista já faz."""
    c.execute("""update orcamentos set token = substr(md5(random()::text || id::text
                   || clock_timestamp()::text), 1, 22)
                 where conta_id=%s and token is null""", (conta_id,))


def orcamentos(pool, conta_id: int, *, membro_id: int | None = None,
               status: str = "", vendedor_id: int | None = None) -> list[dict]:
    """As propostas da conta, com o link público pronto pra mandar.

    `membro_id` setado = carteira DELE (o vendedor só enxerga o que criou); None =
    carteira toda (dono/gestor). É a mesma regra de escopo que painel_servicos.lista
    já usa — `orcamentos.criado_por` guarda o membro_id como TEXTO.

    Traz também o lead de origem quando existe (prospeccao.orcamento_id aponta pra
    cá), que é o que permite mandar a proposta na conversa em vez de só por link.
    """
    where = ["o.conta_id=%s"]
    args: list = [conta_id]
    if membro_id:
        where.append("o.criado_por=%s")
        args.append(str(membro_id))
    elif vendedor_id:
        where.append("o.criado_por=%s")
        args.append(str(vendedor_id))
    if status in STATUS_ORC:
        where.append("coalesce(o.status,'rascunho')=%s")
        args.append(status)
    with pool.connection() as c:
        _curar_tokens(c, conta_id)
        c.commit()
        rows = c.execute(
            """select o.id, o.cliente, o.empresa, o.setup_centavos, o.mensal_centavos,
                      coalesce(o.status,'rascunho'), o.token, o.criado_em, o.criado_por,
                      coalesce(o.whatsapp, o.telefone, ''), o.aprovada_por, o.aprovada_em,
                      p.id, coalesce(nullif(m.nome,''), m.email, '—')
                 from orcamentos o
                 left join prospeccao p on p.orcamento_id = o.id and p.conta_id = o.conta_id
                 left join membros m on m.id::text = o.criado_por and m.conta_id = o.conta_id
                where """ + " and ".join(where) + """
                order by o.criado_em desc limit 100""", tuple(args)).fetchall()
    from web.painel_prospeccao import _zap_link_texto
    out = []
    for r in rows:
        link = _link_proposta(r[6])
        out.append({
            "id": r[0], "cliente": r[1] or "", "empresa": r[2] or "",
            "titulo": (r[2] or r[1] or "Proposta"),   # a empresa na frente: é por ela que se procura
            "setup_centavos": int(r[3] or 0), "mensal_centavos": int(r[4] or 0),
            "status": r[5], "status_rot": _ROT_ORC.get(r[5], r[5].title()),
            "token": r[6] or "", "link": link,
            "criado_em": r[7], "vendedor": r[13],
            "zap": _zap_link_texto(r[9], f"Olá! Segue sua proposta 👋\n{link}") if (r[9] and link) else "",
            "aprovada_por": r[10] or "",
            "aprovada_em": r[11],
            "lead_id": r[12],
        })
    return out


def orcamento(pool, conta_id: int, orc_id: int, *, membro_id: int | None = None) -> dict | None:
    """Uma proposta (com os itens), no mesmo escopo de `orcamentos`."""
    import json as _json
    args: list = [orc_id, conta_id]
    dono = ""
    if membro_id:
        dono = " and o.criado_por=%s"
        args.append(str(membro_id))
    with pool.connection() as c:
        _curar_tokens(c, conta_id)
        c.commit()
        r = c.execute(
            """select o.id, o.cliente, o.empresa, o.cnpj, o.segmento,
                      coalesce(o.whatsapp, o.telefone, ''), o.email, o.itens,
                      o.setup_centavos, o.mensal_centavos, coalesce(o.status,'rascunho'),
                      o.token, o.criado_em, o.aprovada_por, o.aprovada_em, o.aprovada_doc,
                      p.id, coalesce(nullif(m.nome,''), m.email, '—'), o.cidade, o.uf
                 from orcamentos o
                 left join prospeccao p on p.orcamento_id = o.id and p.conta_id = o.conta_id
                 left join membros m on m.id::text = o.criado_por and m.conta_id = o.conta_id
                where o.id=%s and o.conta_id=%s""" + dono, tuple(args)).fetchone()
    if not r:
        return None
    itens = r[7]
    if isinstance(itens, str):
        try:
            itens = _json.loads(itens)
        except ValueError:
            itens = []
    link = _link_proposta(r[11])
    from web.painel_prospeccao import _zap_link_texto
    return {
        "id": r[0], "cliente": r[1] or "", "empresa": r[2] or "",
        "titulo": (r[2] or r[1] or "Proposta"),
        "cnpj": r[3] or "", "segmento": r[4] or "", "whatsapp": r[5] or "", "email": r[6] or "",
        "itens": itens or [], "setup_centavos": int(r[8] or 0), "mensal_centavos": int(r[9] or 0),
        "status": r[10], "status_rot": _ROT_ORC.get(r[10], r[10].title()),
        "token": r[11] or "", "link": link, "criado_em": r[12],
        "aprovada_por": r[13] or "", "aprovada_em": r[14], "aprovada_doc": r[15] or "",
        "lead_id": r[16], "vendedor": r[17], "cidade": r[18] or "", "uf": r[19] or "",
        "zap": _zap_link_texto(r[5], f"Olá! Segue sua proposta 👋\n{link}") if (r[5] and link) else "",
    }


# estados que o funil move na mão. 'fechado' fica de fora de propósito: fechar é
# `fechar_contrato`, que gera os títulos a receber — deixar 'fechado' no seletor
# marcaria a proposta como fechada SEM gerar o contrato, e ninguém perceberia.
STATUS_MANUAIS = ("rascunho", "enviado", "negociando", "aprovada", "perdido")


def mudar_status_orcamento(pool, conta_id: int, orc_id: int, novo: str,
                           *, membro_id: int | None = None) -> dict:
    """Move a proposta no funil. `membro_id` setado limita ao que a pessoa criou —
    é o que impede um vendedor de mexer na proposta de outro pela URL.

    Proposta FECHADA não volta atrás por aqui: os títulos a receber já existem, e
    reabrir pra fechar de novo geraria o contrato em dobro. É a mesma regra do
    painel, que também recusa editar orçamento com status 'fechado'.
    """
    if novo not in STATUS_MANUAIS:
        return {"ok": False, "erro": "Status inválido."}
    args: list = [novo, orc_id, conta_id]
    dono = ""
    if membro_id:
        dono = " and criado_por=%s"
        args.append(str(membro_id))
    with pool.connection() as c:
        n = c.execute("update orcamentos set status=%s, atualizado_em=now() "
                      "where id=%s and conta_id=%s and coalesce(status,'rascunho') <> 'fechado'"
                      + dono, tuple(args)).rowcount
        c.commit()
        if not n:
            atual = c.execute("select coalesce(status,'rascunho') from orcamentos "
                              "where id=%s and conta_id=%s", (orc_id, conta_id)).fetchone()
    if not n:
        if atual and atual[0] == "fechado":
            return {"ok": False, "erro": "Essa proposta já virou contrato — não dá pra reabrir."}
        return {"ok": False, "erro": "Proposta não encontrada (ou não é sua)."}
    return {"ok": True, "status": novo, "msg": f"Proposta marcada como {_ROT_ORC.get(novo, novo)} ✓"}


def fechar_contrato(pool, conta_id: int, orc_id: int, *, membro_id: int | None = None) -> dict:
    """Fecha a proposta como CONTRATO: gera os títulos a receber (entrada + a
    mensalidade recorrente) no módulo Empresa.

    Aqui só mora o escopo — quem gera é `vendas.fechar_orcamento`, o mesmo motor do
    botão do painel, que é atômico (o duplo-clique não gera título em dobro) e
    escopado por conta. O que ele NÃO faz é olhar quem criou a proposta: sem a
    checagem abaixo, um vendedor fecharia contrato da proposta de outro pela URL.
    """
    if membro_id:
        with pool.connection() as c:
            dele = c.execute("select 1 from orcamentos where id=%s and conta_id=%s and criado_por=%s",
                             (orc_id, conta_id, str(membro_id))).fetchone()
        if not dele:
            return {"ok": False, "erro": "Proposta não encontrada (ou não é sua)."}
    from finance import vendas
    r = vendas.fechar_orcamento(pool, conta_id, orc_id, criado_por=membro_id)
    if not r.get("ok"):
        return r
    quantos = sum(1 for k in ("setup_titulo_id", "mensal_titulo_id") if r.get(k))
    return {**r, "status": "fechado",
            "msg": (f"Contrato fechado ✓ {quantos} título(s) a receber gerado(s)"
                    if quantos else "Contrato fechado ✓")}


def criar_orcamento(pool, conta_id: int, membro_id: int, lead_id: int, itens) -> dict:
    """Cria a proposta do lead (mesma tabela/token do painel) e devolve o link público
    /proposta/<token> pro vendedor mandar. Revalida a posse do lead. Reusa a página de
    proposta que já existe (o cliente vê com a marca da empresa e aprova online)."""
    import secrets as _secrets
    from web.painel_prospeccao import _zap_link_texto
    from finance.email_sender import _app_url
    linhas = _sanear_itens(itens)
    if not linhas:
        return {"ok": False, "erro": "Adicione ao menos um item ao orçamento."}
    import json as _json
    setup_c = sum(x["setup"] for x in linhas) * 100
    mensal_c = sum(x["mensal"] for x in linhas) * 100
    with pool.connection() as c:
        if not _posse(c, conta_id, membro_id, lead_id):
            return {"ok": False, "erro": "escopo"}
        lead = c.execute(
            """select empresa, coalesce(nullif(contato,''), decisor_nome, socio), cnpj,
                      segmento, whatsapp, telefone, email, cidade, uf
                 from prospeccao where id=%s and conta_id=%s""", (lead_id, conta_id)).fetchone()
        try:
            from web.painel_servicos import _garantir_tabela
            _garantir_tabela(c)
        except Exception:  # noqa: BLE001 — colunas já existem em produção
            pass
        token = _secrets.token_urlsafe(16)
        oid = c.execute(
            """insert into orcamentos
                 (conta_id, cliente, empresa, cnpj, segmento, whatsapp, telefone, email,
                  cidade, uf, itens, setup_centavos, mensal_centavos, status, criado_por, canal, token)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,'enviado',%s,'cockpit',%s)
               returning id""",
            (conta_id, (lead[1] or None), (lead[0] or None), lead[2], lead[3], lead[4],
             lead[5], lead[6], lead[7], (lead[8] or "")[:2] or None,
             _json.dumps(linhas), setup_c, mensal_c, str(membro_id), token)).fetchone()[0]
        c.execute("update prospeccao set orcamento_id=%s, atualizado_em=now() where id=%s and conta_id=%s",
                  (oid, lead_id, conta_id))
        c.commit()
    link = f"{_app_url()}/proposta/{token}"
    numero = (lead[4] or lead[5] or "")
    msg = f"Olá! Segue sua proposta 👋\n{link}"
    return {"ok": True, "id": oid, "token": token, "link": link,
            "zap": _zap_link_texto(numero, msg) if numero else "",
            "setup_centavos": setup_c, "mensal_centavos": mensal_c}


def enviar_proposta_conversa(pool, conta_id: int, membro_id: int, lead_id: int, link: str) -> dict:
    """Manda o link da proposta na conversa do lead (WhatsApp da empresa)."""
    return enviar_mensagem(pool, conta_id, membro_id, lead_id, f"Olá! Segue sua proposta 👋\n{link}")


# ----------------------------------------------------------------- agendar visita

def _maps_link(local: str) -> str:
    from urllib.parse import quote
    return "https://www.google.com/maps/search/?api=1&query=" + quote(local or "") if local else ""


def endereco_empresa(pool, conta_id: int) -> dict:
    """Endereço do SALÃO (a empresa/conta) pro local da visita — vem do cadastro da
    empresa (Empresa → endereço). Lê direto de `contas` (sem depender de nicho).
    {nome, endereco, maps}."""
    with pool.connection() as c:
        r = c.execute(
            "select coalesce(nome_fantasia,''), coalesce(razao_social,''), coalesce(nome,''), "
            "coalesce(endereco,''), coalesce(bairro,''), coalesce(cidade,''), coalesce(uf,'') "
            "from contas where id=%s", (conta_id,)).fetchone()
    if not r:
        return {"nome": "Nosso espaço", "endereco": "", "maps": ""}
    nome = (r[0] or r[1] or r[2] or "Nosso espaço").strip()
    cid_uf = " ".join(x for x in [r[5].strip(), r[6].strip()] if x)
    endereco = " — ".join(p for p in [r[3].strip(), r[4].strip(), cid_uf] if p)
    return {"nome": nome, "endereco": endereco, "maps": _maps_link(endereco or nome)}


def agendar_visita(pool, conta_id: int, membro_id: int, lead_id: int, *, data: str, hora: str,
                   dur_min: int = 60, local: str = "", lembrete_min: int | None = 60,
                   avisar_cliente: bool = True) -> dict:
    """Marca a visita do lead ao espaço: cria o evento na agenda, liga no lead, move o
    lead pra 'qualificado' e (opcional) manda a confirmação com o endereço + o convite
    .ics pro cliente. Revalida a posse do lead."""
    import secrets as _secrets
    from datetime import datetime, timedelta
    from finance import agenda as ag
    from finance.email_sender import _app_url
    try:
        ini = datetime.fromisoformat(f"{data}T{hora}").replace(tzinfo=ag.BRT)
    except Exception:  # noqa: BLE001
        return {"ok": False, "erro": "Data ou hora inválida."}
    dur = max(15, int(dur_min or 60))
    fim = ini + timedelta(minutes=dur)
    esp = endereco_empresa(pool, conta_id)
    local = (local or "").strip() or esp["endereco"] or esp["nome"]
    with pool.connection() as c:
        if not _posse(c, conta_id, membro_id, lead_id):
            return {"ok": False, "erro": "escopo"}
        lead = c.execute("select coalesce(nullif(contato,''), nullif(empresa,''), 'Cliente'), "
                         "coalesce(whatsapp, telefone, '') from prospeccao where id=%s and conta_id=%s",
                         (lead_id, conta_id)).fetchone()
    quem, numero = (lead[0], lead[1]) if lead else ("Cliente", "")
    descricao = (f"Visita de {quem} ao {esp['nome']}.\nLocal: {local}"
                 + (f"\nMapa: {esp['maps']}" if esp["maps"] else ""))
    lembrete = int(lembrete_min) if lembrete_min else None
    ev = ag.criar_evento(pool, conta_id, f"Visita — {quem}", ini, membro_id=membro_id, fim=fim,
                         local=local, descricao=descricao, lembrete_min=lembrete, tipo="empresa")
    token = _secrets.token_urlsafe(12)
    quando = ini.astimezone(ag.BRT).strftime("%d/%m às %H:%M")
    with pool.connection() as c:
        c.execute("update eventos_agenda set prospeccao_id=%s, ics_token=%s where id=%s and conta_id=%s",
                  (lead_id, token, ev["id"], conta_id))
        try:
            c.execute("""insert into prospeccao_atividades (prospeccao_id, membro_id, tipo, resultado, descricao)
                         values (%s,%s,'visita','agendado',%s)""",
                      (lead_id, membro_id, f"Visita agendada: {quando} — {local}"[:400]))
        except Exception:  # noqa: BLE001
            pass
        # ao agendar a visita, o lead avança pra 'qualificado' (nunca mexe em ganho/perdido)
        c.execute("update prospeccao set status='qualificado', ultimo_contato_em=now(), atualizado_em=now() "
                  "where id=%s and conta_id=%s and status not in ('ganho','perdido')", (lead_id, conta_id))
        c.commit()
    ics_url = f"{_app_url()}/visita/{token}.ics"
    msg = (f"Olá! 👋 Sua visita ao {esp['nome']} está marcada:\n📅 {quando}\n📍 {local}"
           + (f"\n🗺️ {esp['maps']}" if esp["maps"] else "")
           + f"\n📎 Adicione ao seu calendário: {ics_url}\nAté lá! 😊")
    avisado = False
    if avisar_cliente and numero:
        try:
            from finance import whatsapp_out as wo
            from web.painel_prospeccao import _registrar_msg
            with pool.connection() as c:
                res = wo.enviar(c, conta_id, numero, msg)
                if res.get("ok"):
                    _registrar_msg(c, conta_id, lead_id, "whatsapp", "out", "humano", msg, membro_id, res.get("sid"))
                    c.commit()
                    avisado = True
        except Exception:  # noqa: BLE001
            avisado = False
    from web.painel_prospeccao import _zap_link_texto
    return {"ok": True, "evento_id": ev["id"], "ics_url": ics_url, "quando": quando,
            "local": local, "empresa": esp["nome"], "avisado": avisado,
            "zap": _zap_link_texto(numero, msg) if numero else ""}


def visita_ics(pool, token: str) -> str | None:
    """.ics público da visita (o cliente abre o link e salva no calendário DELE). Inclui
    VALARM (1 dia e 2h antes) — é o lembrete do cliente. None se o token não existe."""
    token = (token or "").strip()
    if not token:
        return None
    from finance import agenda as ag
    with pool.connection() as c:
        r = c.execute("select id, titulo, inicio, fim, local, descricao, criado_em "
                      "from eventos_agenda where ics_token=%s and status='ativo'", (token,)).fetchone()
    if not r:
        return None
    ev = {"id": r[0], "titulo": r[1], "inicio": r[2], "fim": r[3], "local": r[4],
          "descricao": r[5], "criado_em": r[6]}
    vevent = ag.evento_para_ics(ev)
    alarmes = ("BEGIN:VALARM\r\nACTION:DISPLAY\r\nDESCRIPTION:Lembrete da visita\r\nTRIGGER:-P1D\r\nEND:VALARM\r\n"
               "BEGIN:VALARM\r\nACTION:DISPLAY\r\nDESCRIPTION:Lembrete da visita\r\nTRIGGER:-PT2H\r\nEND:VALARM")
    vevent = vevent.replace("END:VEVENT", alarmes + "\r\nEND:VEVENT")
    cab = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Zaq//Visita//PT", "CALSCALE:GREGORIAN", "METHOD:PUBLISH"]
    return "\r\n".join(cab + [vevent, "END:VCALENDAR"]) + "\r\n"


# ------------------------------------------------------------------ agenda do vendedor
def visitas_do_vendedor(pool, conta_id: int, membro_id: int, dias: int = 14) -> list[dict]:
    """As visitas QUE ELE marcou, de hoje pra frente (a agenda do dia dele).

    `agendar_visita` grava o evento com `membro_id` (via `agenda.criar_evento`) e
    liga no lead pelo `prospeccao_id`; aqui a gente lê o mesmo dado pelo dono do
    evento. `agenda.listar_eventos` não serve porque é por CONTA — o vendedor
    veria a agenda do time inteiro.
    """
    from finance import agenda as ag
    from web.painel_prospeccao import _zap_link
    hoje = datetime.now(ag.BRT).replace(hour=0, minute=0, second=0, microsecond=0)
    with pool.connection() as c:
        rows = c.execute(
            """select e.id, e.titulo, e.inicio, e.local, e.ics_token,
                      e.prospeccao_id, p.empresa, coalesce(p.whatsapp, p.telefone, '')
                 from eventos_agenda e
                 left join prospeccao p on p.id = e.prospeccao_id and p.conta_id = e.conta_id
                where e.conta_id=%s and e.membro_id=%s and e.status='ativo'
                  and e.inicio >= %s and e.inicio < %s
                order by e.inicio""",
            (conta_id, membro_id, hoje, hoje + timedelta(days=max(1, int(dias or 14))))).fetchall()
    out = []
    for r in rows:
        ini = r[2].astimezone(ag.BRT) if r[2] else None
        out.append({
            "id": r[0], "titulo": r[1] or "Visita", "inicio": ini,
            "dia": ini.strftime("%d/%m") if ini else "", "hora": ini.strftime("%H:%M") if ini else "",
            "hoje": bool(ini and ini.date() == hoje.date()),
            "local": r[3] or "", "maps": _maps_link(r[3] or ""),
            "ics_url": f"/visita/{r[4]}.ics" if r[4] else "",
            "lead_id": r[5], "empresa": r[6] or "", "zap": _zap_link(r[7]) if r[7] else "",
        })
    return out


# ------------------------------------------------------------------ o resultado DELE
def remuneracao(pool, conta_id: int, membro_id: int, periodo: str = "mes") -> dict:
    """O que o vendedor fechou no período e quanto disso é comissão dele.

    ATENÇÃO à fonte: o número sai de `prospeccao.valor_estimado_centavos` dos leads
    que ELE marcou como ganho — a mesma base do Placar do dono. NÃO é a mesma base do
    relatório de comissão do painel, que soma `lancamentos.membro_id`. Enquanto as
    duas não forem unificadas, a tela precisa dizer de onde veio o número (quem chama
    isso mostra o rótulo), em vez de exibir um valor que não bate com o do dono.
    """
    from finance import cockpit_dono as cd
    ordem = cd.placar(pool, conta_id, periodo)          # já vem ordenado por R$ fechado
    linha = next((p for p in ordem if p["id"] == membro_id), None)
    with pool.connection() as c:
        r = c.execute("select comissao_pct from membros where id=%s and conta_id=%s",
                      (membro_id, conta_id)).fetchone()
    pct = float(r[0]) if (r and r[0] is not None) else None
    fechado = int((linha or {}).get("rs_centavos") or 0)
    posicao = next((i + 1 for i, p in enumerate(ordem) if p["id"] == membro_id), None)
    return {
        "fechado_centavos": fechado,
        "ganhos": int((linha or {}).get("ganhos") or 0),
        "conversao": (linha or {}).get("conversao") or "—",
        "resp": (linha or {}).get("resp") or "—",
        "fila": int((linha or {}).get("fila") or 0),
        "comissao_pct": pct,
        "comissao_centavos": (round(fechado * pct / 100) if pct else None),
        "posicao": posicao, "total_equipe": len(ordem),
    }
