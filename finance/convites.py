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
from datetime import timedelta

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


def registrar_mensagem(pool, conta_id: int, evento_id: int | None, convidado_id: int | None,
                       tipo: str, canal: str, ok: bool, motivo: str | None = None) -> None:
    """Loga uma tentativa de envio (convite/lembrete/remarcado) pro histórico do
    painel ("Histórico de envios") — só um LOG, nunca decide comportamento de
    envio (isso continua em lembretes_enviados / status do convidado). Best-
    effort: nunca levanta, pra um problema no log não derrubar o envio de
    verdade."""
    try:
        with pool.connection() as c:
            c.execute(
                "insert into agenda_mensagens_log "
                "(conta_id, evento_id, convidado_id, tipo, canal, ok, motivo) "
                "values (%s,%s,%s,%s,%s,%s,%s)",
                (conta_id, evento_id, convidado_id, tipo, canal, ok, motivo))
            c.commit()
    except Exception:  # noqa: BLE001
        pass


def listar_historico(pool, conta_id: int, dias: int = 7, somente_falhas: bool = False,
                     busca: str = "", limite: int = 20, offset: int = 0) -> dict:
    """Histórico de envios (convite/lembrete/remarcado) pro painel — junta com o
    evento e o convidado só pra exibição. Mais recentes primeiro."""
    desde = ag.agora_brt() - timedelta(days=dias)
    where = ["l.conta_id=%s", "l.criado_em >= %s"]
    params: list = [conta_id, desde]
    if somente_falhas:
        where.append("l.ok = false")
    busca = (busca or "").strip()
    if busca:
        where.append("(e.titulo ilike %s or g.nome ilike %s)")
        params += [f"%{busca}%", f"%{busca}%"]
    sql_where = " and ".join(where)
    with pool.connection() as c:
        total = c.execute(
            f"select count(*) from agenda_mensagens_log l "
            f"left join eventos_agenda e on e.id = l.evento_id "
            f"left join evento_convidados g on g.id = l.convidado_id "
            f"where {sql_where}", params).fetchone()[0]
        rows = c.execute(
            f"select l.id, l.criado_em, l.tipo, l.canal, l.ok, l.motivo, "
            f"       e.titulo, e.local, g.nome "
            f"  from agenda_mensagens_log l "
            f"  left join eventos_agenda e on e.id = l.evento_id "
            f"  left join evento_convidados g on g.id = l.convidado_id "
            f" where {sql_where} order by l.criado_em desc limit %s offset %s",
            params + [limite, offset]).fetchall()
    itens = [{"id": r[0], "quando": r[1], "tipo": r[2], "canal": r[3], "ok": r[4],
             "motivo": r[5], "evento_titulo": r[6], "evento_local": r[7],
             "convidado_nome": r[8]} for r in rows]
    return {"itens": itens, "total": total}


def listar_fila(pool, conta_id: int, agora, horizonte_dias: int = 7) -> list[dict]:
    """Fila do que ainda vai sair — dono e convidados confirmados de eventos
    futuros que ainda não têm o aviso registrado em lembretes_enviados (mesma
    fonte que o motor usa pra dedup, então nunca desalinha do que ele realmente
    vai fazer). `sai_em` é a hora em que a janela abre (evento.inicio menos
    aviso_antes_min) — pode já ter passado (evento na janela, ainda tentando a
    cada ciclo, sem sucesso ainda) sem sumir da fila."""
    cfg = ag.get_config(pool, conta_id)
    antes_min = cfg.get("aviso_antes_min")
    if not antes_min:
        return []
    eventos = ag.listar_eventos(pool, conta_id, agora, agora + timedelta(days=horizonte_dias))
    if not eventos:
        return []
    ids = [e["id"] for e in eventos]
    with pool.connection() as c:
        avisados = {(t, k) for (t, k) in c.execute(
            "select tipo, chave from lembretes_enviados "
            "where conta_id=%s and tipo in ('aviso','aviso_convidado')", (conta_id,)).fetchall()}
    convidados = por_evento(pool, conta_id, ids) if cfg.get("avisar_convidados") else {}
    fila = []
    for ev in eventos:
        sai_em = ev["inicio"] - timedelta(minutes=antes_min)
        if ("aviso", f"evt:{ev['id']}") not in avisados:
            fila.append({"evento_id": ev["id"], "evento_titulo": ev["titulo"],
                        "evento_local": ev.get("local"), "convidado_nome": None,
                        "tipo": "lembrete", "sai_em": sai_em})
        for g in convidados.get(ev["id"], []):
            if g["status"] != "confirmado" or not (g.get("contato") or "").strip():
                continue
            if ("aviso_convidado", f"evt:{ev['id']}:conv:{g['id']}") not in avisados:
                fila.append({"evento_id": ev["id"], "evento_titulo": ev["titulo"],
                            "evento_local": ev.get("local"), "convidado_nome": g.get("nome"),
                            "tipo": "lembrete", "sai_em": sai_em})
    fila.sort(key=lambda x: x["sai_em"])
    return fila


def reenviar_historico(pool, conta_id: int, log_id: int) -> dict:
    """Reenvia manualmente uma tentativa que falhou no Histórico de envios — não
    espera o próximo ciclo do motor (~2min) nem a janela do evento. Só 'convite'
    e 'lembrete' têm reenvio manual aqui; 'remarcado' já tem seu próprio botão
    de Remarcar no compromisso."""
    with pool.connection() as c:
        row = c.execute(
            "select tipo, evento_id, convidado_id from agenda_mensagens_log "
            "where id=%s and conta_id=%s", (log_id, conta_id)).fetchone()
    if not row:
        return {"ok": False, "erro": "nao_encontrado"}
    tipo, evento_id, convidado_id = row
    if tipo not in ("convite", "lembrete"):
        return {"ok": False, "erro": "tipo_nao_suportado"}
    if tipo == "convite":
        if not convidado_id:
            return {"ok": False, "erro": "nao_encontrado"}
        with pool.connection() as c:
            tok = c.execute("select token from evento_convidados where id=%s and conta_id=%s",
                            (convidado_id, conta_id)).fetchone()
        if not tok:
            return {"ok": False, "erro": "nao_encontrado"}
        return enviar_convite_whatsapp(pool, tok[0])
    ev = ag.evento_por_id(pool, conta_id, evento_id)
    if not ev:
        return {"ok": False, "erro": "nao_encontrado"}
    agora = ag.agora_brt()
    hora = ag.fmt_hora(ev)
    faltam = max(0, int((ev["inicio"] - agora).total_seconds() // 60))
    if convidado_id is None:
        loc = f"\n📍 {ev['local']}" if ev.get("local") else ""
        txt = f"⏰ *Daqui a pouco* (em ~{faltam} min): *{ev['titulo']}* às {hora}.{loc}"
        from . import notificar
        ok = notificar.enviar_para_dono(pool, conta_id, txt)
        registrar_mensagem(pool, conta_id, evento_id, None, "lembrete", "telegram",
                           ok, None if ok else "falha_envio")
        return {"ok": ok}
    g = next((x for x in por_evento(pool, conta_id, [evento_id]).get(evento_id, [])
              if x["id"] == convidado_id), None)
    if not g:
        return {"ok": False, "erro": "nao_encontrado"}
    return avisar_convidado_confirmado(pool, conta_id, ev, g, hora, faltam, agora)


def por_token(pool, token: str) -> dict | None:
    """Tudo que a página pública de confirmação precisa: convidado + evento + a
    empresa (nome COMERCIAL). None se o token não existe.

    O nome é o que o CONVIDADO vê — no título da página, no "A {empresa} quer
    marcar com você" e no assunto do convite por WhatsApp/e-mail (ver
    _titulo_com_extras). Por isso a ordem é nome_fantasia → razão social →
    contas.nome, e não `contas.nome` direto: esse último é o nome de quem ABRIU
    a conta, então o convite da Prime Eventos chegava assinado "MANOEL SOARES" —
    um nome que o cliente do salão não tem como reconhecer.

    Mesma precedência que web/proposta.py já usa no cabeçalho do orçamento de
    evento, pelo mesmo motivo. O coalesce cobre a conta PF, que não tem fantasia
    nem razão social: aí o nome da pessoa É o nome comercial."""
    token = (token or "").strip()
    if not token:
        return None
    with pool.connection() as c:
        r = c.execute(
            """select cv.id, cv.nome, cv.contato, cv.status, cv.resposta, cv.token,
                      cv.conta_id, e.id, e.titulo, e.inicio, e.fim, e.local, e.tipo,
                      coalesce(nullif(btrim(co.nome_fantasia), ''),
                               nullif(btrim(co.razao_social), ''),
                               co.nome), e.link_online
                 from evento_convidados cv
                 join eventos_agenda e on e.id = cv.evento_id
                 join contas co on co.id = cv.conta_id
                where cv.token = %s""", (token,)).fetchone()
    if not r:
        return None
    ev = {"id": r[7], "titulo": r[8], "inicio": r[9], "fim": r[10],
          "local": r[11], "tipo": r[12] or "pessoal", "link_online": r[14]}
    return {"id": r[0], "nome": r[1], "contato": r[2], "status": r[3],
            "resposta": r[4], "token": r[5], "conta_id": r[6],
            "evento": ev, "empresa": r[13]}


def responder(pool, token: str, status: str, resposta: str | None = None,
              canal: str = "web") -> dict | None:
    """Registra a resposta do convidado (confirmado/remarcar/recusado). Devolve o
    convidado atualizado (com evento + empresa) pra avisar o dono, ou None.

    `canal` ('web' ou 'whatsapp') importa pro "aviso antes da reunião" mais tarde:
    só uma resposta que veio de verdade pelo WhatsApp abre a janela de 24h de
    texto livre — confirmar pela página pública (sem login) não abre sessão
    nenhuma, mesmo que pareça "recente" (ver _dentro_da_janela).

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
            "update evento_convidados set status=%s, resposta=%s, respondido_em=now(), "
            "respondido_canal=%s where token=%s",
            (status, (resposta or "").strip() or None, canal, tok))
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
            "select evento_id, id, nome, contato, status, token, respondido_em, respondido_canal "
            "from evento_convidados where conta_id=%s and evento_id = any(%s) order by id",
            (conta_id, list(evento_ids))).fetchall()
    out: dict[int, list[dict]] = {}
    for r in rows:
        out.setdefault(r[0], []).append(
            {"evento_id": r[0], "id": r[1], "nome": r[2], "contato": r[3],
             "status": r[4], "status_rot": STATUS_ROT.get(r[4], "Aguardando"),
             "token": r[5], "respondido_em": r[6], "respondido_canal": r[7]})
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


def link_mapa(ev: dict) -> str | None:
    """Link do Google Maps pro local do evento — None se o evento não tem local
    (reunião online, por exemplo)."""
    return ag.link_maps(ev.get("local"))


def url_convite(token: str) -> str:
    """Link público /convite/<token> a partir do APP_URL (pro agente, que não tem
    request). Sem APP_URL, devolve o caminho relativo."""
    base = (os.environ.get("APP_URL") or "").rstrip("/")
    return f"{base}/convite/{token}" if base else f"/convite/{token}"


_ERRO_ENVIO_ROT = {
    "sem_numero": "esse convidado está sem número de WhatsApp",
    "sem_template": "o disparo automático ainda não está ligado",
    "provedor_sem_template": "o número da empresa não é Twilio (o template só sai por Twilio)",
    "nao_configurado": "o WhatsApp não está configurado",
    "sem_numero_empresa": "a empresa ainda não tem número de WhatsApp",
    "numero_invalido": "o número do convidado parece inválido",
}


def motivo_erro(codigo: str) -> str:
    """Traduz o código de erro do envio pra algo que dá pra mostrar a humano."""
    return _ERRO_ENVIO_ROT.get(codigo, codigo or "erro desconhecido")


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
        txt = (f"✅ {oi}presença confirmada em *{ev['titulo']}* — {quando}! "
               f"Obrigado 🙌\n\n📆 Adicionar ao seu calendário: {link_calendario(ev)}")
        mapa = link_mapa(ev)
        if mapa:
            txt += f"\n📍 Ver o local no mapa: {mapa}"
        if ev.get("link_online"):
            txt += f"\n🎥 Entrar na chamada: {ev['link_online']}"
        return txt
    if st == "remarcar":
        return (f"🔁 Anotado{(' ' + primeiro) if primeiro else ''}! Vou avisar o "
                f"organizador que você precisa remarcar *{ev['titulo']}* ({quando}) "
                f"pra combinarem um novo horário. 👍")
    return (f"❌ Tudo bem{(', ' + primeiro) if primeiro else ''}! Anotei que você não "
            f"vai poder em *{ev['titulo']}* ({quando}). Obrigado por avisar! 🙏")


# ---- Disparo do convite pelo WhatsApp (outbound, via template aprovado) -------
#
# Precedência do template: COLUNA da empresa > env global. O template é aprovado
# dentro da conta vinculada ao NÚMERO, e cada empresa tem o seu — um SID aprovado
# no número A não vale no número B. A env global fica como fallback pra não
# quebrar quem já usa. Mesmo desenho de prospec_convite.sid_efetivo.


def _sid_do_canal(pool, conta_id: int, coluna: str) -> str:
    """SID salvo pra ESTA empresa em canais_config. '' se não tem (ou se a coluna
    ainda não existe — degrada em vez de quebrar, igual config_app.get_config)."""
    if not pool or not conta_id:
        return ""
    try:
        with pool.connection() as c:
            r = c.execute(
                f"select {coluna} from canais_config "          # noqa: S608 - coluna é literal do módulo
                "where conta_id=%s and canal='whatsapp' and ativo", (conta_id,)).fetchone()
        return (r[0] or "").strip() if r else ""
    except Exception:  # noqa: BLE001
        return ""


def sid_convite(pool, conta_id: int) -> str:
    """Template do CONVITE de reunião que vale pra essa empresa."""
    return (_sid_do_canal(pool, conta_id, "tmpl_convite_sid")
            or (os.environ.get("TWILIO_TMPL_CONVITE_SID") or "").strip())


def sid_lembrete(pool, conta_id: int) -> str:
    """Template do AVISO ANTES da reunião que vale pra essa empresa."""
    return (_sid_do_canal(pool, conta_id, "tmpl_lembrete_sid")
            or (os.environ.get("TWILIO_TMPL_LEMBRETE_SID") or "").strip())


def template_configurado(pool=None, conta_id: int | None = None) -> bool:
    """True quando dá pra o Zaq disparar o convite sozinho. No provedor QR isso
    independe de template (o convite vai como texto livre); nos outros, precisa do
    template aprovado (da empresa ou da env) + credenciais Twilio. Enquanto for
    False, a UI mostra só o envio manual (link)."""
    if _texto_livre_sempre(pool, conta_id):
        return True
    from . import whatsapp_twilio as wa
    return bool(sid_convite(pool, conta_id) and wa.configurado())


def _titulo_com_extras(pool, c: dict) -> str:
    """'{título} — {empresa} (com Fulano e Beltrano)' — empresa e envolvidos
    encaixados no PRÓPRIO texto do assunto, já que o template aprovado no
    Twilio só tem {{1}} título e {{2}} data/hora (sem linha própria pra isso;
    uma linha rotulada exigiria um template novo aprovado)."""
    ev = c["evento"]
    partes = [ev["titulo"]]
    if c.get("empresa"):
        partes.append(f"— {c['empresa']}")
    outros = por_evento(pool, c["conta_id"], [ev["id"]]).get(ev["id"], [])
    nomes = [g["nome"] for g in outros if (g.get("nome") or "").strip()]
    if len(nomes) > 1:
        partes.append(f"(com {ag.frase_nomes(nomes)})")
    return " ".join(partes)


def texto_convite(pool, c: dict) -> str:
    """Convite em texto livre — pro provedor QR, que não tem template.

    Espelha a mensagem que o botão de compartilhar já monta em
    painel_agenda._montar_share, pra quem recebe ver a mesma coisa pelos dois
    caminhos (manual e automático)."""
    ev = c["evento"]
    nome = (c.get("nome") or "").strip()
    quando = ag.fmt_hora(ev)
    local = (ev.get("local") or "").strip()
    outros = por_evento(pool, c["conta_id"], [ev["id"]]).get(ev["id"], [])
    nomes = [g["nome"] for g in outros if (g.get("nome") or "").strip()]
    com = f" Com: {ag.frase_nomes(nomes)}." if len(nomes) > 1 else ""
    onde = f" ({local})" if local and not ag.eh_online(local) else ""
    txt = (f"Oi{(' ' + nome) if nome else ''}! Quero marcar uma reunião: "
           f"{ev['titulo']} — {quando}{onde}.{com} "
           f"Confirma pra mim aqui: {url_convite(c['token'])}")
    if ev.get("link_online"):
        txt += f"\n🎥 Chamada: {ev['link_online']}"
    return txt


def enviar_convite_whatsapp(pool, token: str) -> dict:
    """Dispara o convite pro número do convidado, PELO NÚMERO DA EMPRESA
    (canais_config). No provedor QR vai como texto livre (lá não existe template
    nem janela de 24h); nos outros, pelo template aprovado, que funciona 'frio'.

    As variáveis do template batem com o corpo aprovado no Twilio, NA ORDEM:
    {{1}} assunto/título (com empresa e envolvidos embutidos — veja
    _titulo_com_extras) · {{2}} data e horário. Retorno tolerante — nunca
    levanta; devolve {'ok': bool, ...}."""
    from . import whatsapp_out as wout
    c = por_token(pool, token)
    if not c:
        return {"ok": False, "erro": "convite_nao_encontrado"}
    ev_id, conv_id, conta_id = c["evento"]["id"], c["id"], c["conta_id"]
    if not (c.get("contato") or "").strip():
        registrar_mensagem(pool, conta_id, ev_id, conv_id, "convite", "whatsapp_template",
                           False, "sem_numero")
        return {"ok": False, "erro": "sem_numero"}
    if _texto_livre_sempre(pool, conta_id):        # QR: sem template, texto livre
        with pool.connection() as conn:
            r = wout.enviar(conn, conta_id, c["contato"], texto_convite(pool, c))
        registrar_mensagem(pool, conta_id, ev_id, conv_id, "convite", "whatsapp_livre",
                           bool(r.get("ok")), None if r.get("ok") else r.get("erro"))
        return r
    sid = sid_convite(pool, conta_id)
    if not sid:
        registrar_mensagem(pool, conta_id, ev_id, conv_id, "convite", "whatsapp_template",
                           False, "sem_template")
        return {"ok": False, "erro": "sem_template"}
    ev = c["evento"]
    variaveis = {"1": _titulo_com_extras(pool, c), "2": ag.fmt_hora(ev)}
    with pool.connection() as conn:
        r = wout.enviar_template(conn, conta_id, c["contato"], sid, variaveis)
    registrar_mensagem(pool, conta_id, ev_id, conv_id, "convite", "whatsapp_template",
                       bool(r.get("ok")), None if r.get("ok") else r.get("erro"))
    return r


# ---- Aviso "seu compromisso começa em breve" pros CONFIRMADOS (opt-in) --------
#
# O convidado que confirmou NÃO é lead frio: SE a própria confirmação foi uma
# MENSAGEM DE WHATSAPP dele, isso abre a janela de 24h pra responder de graça,
# sem template. Mas confirmar pela página pública (/convite/<token>, sem login)
# NÃO é mensagem nenhuma — não abre sessão de WhatsApp nenhuma, mesmo que
# pareça "recente". Por isso o canal importa, não só o horário: só respondido
# de verdade PELO WHATSAPP conta como "dentro da janela".
#
# Tudo isso, porém, é regra da API OFICIAL (Business API — twilio/cloud). No
# provedor 'qr' (sessão tipo WhatsApp Web) não existe janela de 24h nem
# template: manda texto livre pra qualquer número, sempre. Por isso o provedor
# é checado ANTES da janela — exigir template de quem usa QR reprovaria um envio
# que ia funcionar.

JANELA_SESSAO = timedelta(hours=24)


def _dentro_da_janela(respondido_em, respondido_canal, agora) -> bool:
    return (respondido_canal == "whatsapp" and bool(respondido_em)
           and (agora - respondido_em) < JANELA_SESSAO)


def _texto_livre_sempre(pool, conta_id: int | None) -> bool:
    """True quando o provedor da conta não tem janela de 24h nem template (QR).
    Tolera pool/conta ausentes (template_configurado pode ser chamada sem eles)."""
    if not pool or not conta_id:
        return False
    from . import whatsapp_out as wout
    try:
        with pool.connection() as c:
            return wout.provedor_da_conta(c, conta_id) == "qr"
    except Exception:  # noqa: BLE001 — canal ausente/erro de leitura: degrada
        return False


def texto_lembrete_convidado(nome: str, ev: dict, hora: str, faltam_min: int) -> str:
    """Mesmo tom do aviso que já mandamos pro dono, adaptado pro convidado.

    Leva o que a pessoa precisa NA HORA de sair/entrar: o mapa (presencial) ou o
    link da chamada (online) — nunca os dois, porque são excludentes — e o
    calendário. É a mesma trinca que a confirmação já mandava, mas o lembrete
    ficava só com título e horário, justamente no momento em que esses links
    mais servem."""
    primeiro = (nome or "").split()[0] if nome else ""
    oi = f"{primeiro}, s" if primeiro else "S"
    txt = f"⏰ {oi}eu compromisso *{ev['titulo']}* começa em ~{faltam_min} min, às {hora}."
    if ev.get("link_online"):
        txt += f"\n🎥 Entrar na chamada: {ev['link_online']}"
    else:
        mapa = link_mapa(ev)
        if mapa:
            txt += f"\n📍 {ev['local']}\n🗺️ Ver no mapa: {mapa}"
    txt += f"\n📆 Adicionar ao calendário: {link_calendario(ev)}"
    return txt


def template_lembrete_configurado(pool=None, conta_id: int | None = None) -> bool:
    """True quando dá pra o Zaq avisar o convidado FORA da janela de 24h (template
    aprovado no Twilio/Meta, da empresa ou da env). Não bloqueia o aviso DENTRO da
    janela — esse sai livre, sem template, sem essa configuração."""
    from . import whatsapp_twilio as wa
    return bool(sid_lembrete(pool, conta_id) and wa.configurado())


def local_rotulo(ev: dict) -> str:
    """Local em uma linha pro corpo do template — nunca vazio, porque a Meta
    recusa variável em branco (e o Twilio manda '-' no lugar)."""
    if ev.get("link_online"):
        return "Reunião online"
    local = (ev.get("local") or "").strip()
    return local or "Sem local definido"


def avisar_convidado_confirmado(pool, conta_id: int, ev: dict, g: dict,
                                hora: str, faltam_min: int, agora) -> dict:
    """Avisa o convidado CONFIRMADO que o compromisso tá chegando.

    No provedor QR (WhatsApp Web): texto livre sempre — lá não existe janela nem
    template. Na API oficial, dentro da janela de 24h desde uma confirmação DE
    VERDADE pelo WhatsApp: manda texto LIVRE (sem template, sem custo extra — é
    só uma resposta dentro da conversa já aberta). Fora dela (inclui QUALQUER
    confirmação pela página pública, que nunca abre sessão): precisa do template
    aprovado (TWILIO_TMPL_LEMBRETE_SID); sem ele, não manda (erro tolerante,
    nunca levanta).

    Recebe o evento e o convidado inteiros porque o texto agora leva mapa /
    chamada / calendário — que dependem de local e link_online."""
    from . import whatsapp_out as wout
    evento_id, convidado_id = ev["id"], g["id"]
    contato = (g.get("contato") or "").strip()
    if not contato:
        registrar_mensagem(pool, conta_id, evento_id, convidado_id, "lembrete", "whatsapp_livre",
                           False, "sem_numero")
        return {"ok": False, "erro": "sem_numero"}
    if _texto_livre_sempre(pool, conta_id) or _dentro_da_janela(g.get("respondido_em"),
                                                               g.get("respondido_canal"), agora):
        texto = texto_lembrete_convidado(g.get("nome"), ev, hora, faltam_min)
        with pool.connection() as conn:
            r = wout.enviar(conn, conta_id, contato, texto)
        registrar_mensagem(pool, conta_id, evento_id, convidado_id, "lembrete", "whatsapp_livre",
                           bool(r.get("ok")), None if r.get("ok") else r.get("erro"))
        return r
    sid = sid_lembrete(pool, conta_id)
    if not sid:
        registrar_mensagem(pool, conta_id, evento_id, convidado_id, "lembrete", "whatsapp_template",
                           False, "fora_da_janela_sem_template")
        return {"ok": False, "erro": "fora_da_janela_sem_template"}
    # {{4}} local (sempre preenchido) e {{5}} token do convite — o botão do
    # template aponta pra /convite/<token>, que já mostra mapa, chamada e
    # calendário. Um template só atende presencial e online: fosse o mapa direto
    # no botão, evento online deixaria a variável vazia (a Meta recusa).
    variaveis = {"1": ev["titulo"], "2": hora, "3": str(faltam_min),
                 "4": local_rotulo(ev), "5": g["token"]}
    with pool.connection() as conn:
        r = wout.enviar_template(conn, conta_id, contato, sid, variaveis)
    registrar_mensagem(pool, conta_id, evento_id, convidado_id, "lembrete", "whatsapp_template",
                       bool(r.get("ok")), None if r.get("ok") else r.get("erro"))
    return r


# ---- Remarcar: muda a data mantendo os mesmos convidados/link -----------------

def texto_remarcado(nome: str, titulo: str, hora_antiga: str, hora_nova: str, url: str,
                    link_online: str | None = None) -> str:
    primeiro = (nome or "").split()[0] if nome else ""
    oi = f"{primeiro}, o" if primeiro else "O"
    txt = (f"🔁 {oi} compromisso *{titulo}* mudou de horário: agora é {hora_nova} "
           f"(antes era {hora_antiga}).\n\nSeu link de confirmação continua o mesmo — "
           f"só confirma de novo quando puder: {url}")
    if link_online:
        txt += f"\n🎥 Entrar na chamada: {link_online}"
    return txt


def avisar_convidado_remarcado(pool, conta_id: int, g: dict, ev: dict,
                               hora_antiga: str, agora) -> dict:
    """Avisa 1 convidado que o compromisso mudou de data. No provedor QR, ou
    dentro da janela de 24h desde a última resposta dele: texto livre,
    reaproveitando o mesmo link. Fora da janela (ou quem nunca respondeu): reusa
    o template de convite já aprovado — mesmo caminho 'frio' do primeiro convite,
    só que a essa altura o evento já está com a data nova, então quem clicar vê o
    horário certo."""
    contato = (g.get("contato") or "").strip()
    if not contato:
        registrar_mensagem(pool, conta_id, ev["id"], g["id"], "remarcado", "whatsapp_livre",
                           False, "sem_numero")
        return {"ok": False, "erro": "sem_numero"}
    if _texto_livre_sempre(pool, conta_id) or _dentro_da_janela(g.get("respondido_em"),
                                                                g.get("respondido_canal"), agora):
        from . import whatsapp_out as wout
        url = url_convite(g["token"])
        texto = texto_remarcado(g.get("nome"), ev["titulo"], hora_antiga, ag.fmt_hora(ev), url,
                                ev.get("link_online"))
        with pool.connection() as conn:
            r = wout.enviar(conn, conta_id, contato, texto)
        registrar_mensagem(pool, conta_id, ev["id"], g["id"], "remarcado", "whatsapp_livre",
                           bool(r.get("ok")), None if r.get("ok") else r.get("erro"))
        return r
    return enviar_convite_whatsapp(pool, g["token"])   # fora da janela: cai no template de convite (já loga tipo='convite')


def remarcar_e_avisar(pool, conta_id: int, evento_id: int, novo_inicio, novo_fim,
                      avisar: bool, agora) -> dict:
    """Muda a data do evento (finance.agenda.remarcar_evento) e, se `avisar`,
    notifica cada convidado com contato salvo pelo WhatsApp. A confirmação/recusa
    antiga valia pra outro horário, então o status de TODOS os convidados volta
    pra 'pendente' (independe de `avisar` — é sobre o evento ter mudado, não
    sobre ter avisado). Tolerante: nunca levanta, um convidado sem número ou fora
    da janela sem template só não é avisado, os outros seguem normalmente."""
    ev_antigo = ag.evento_por_id_qualquer_status(pool, conta_id, evento_id)
    if not ev_antigo:
        return {"ok": False}
    hora_antiga = ag.fmt_hora(ev_antigo)
    if not ag.remarcar_evento(pool, conta_id, evento_id, novo_inicio, novo_fim):
        return {"ok": False}
    guests = por_evento(pool, conta_id, [evento_id]).get(evento_id, [])
    if guests:
        with pool.connection() as c:
            c.execute("update evento_convidados set status='pendente', resposta=null "
                      "where evento_id=%s and conta_id=%s", (evento_id, conta_id))
            c.commit()
    avisados = 0
    if avisar and guests:
        ev_novo = ag.evento_por_id(pool, conta_id, evento_id)
        for g in guests:
            try:
                if avisar_convidado_remarcado(pool, conta_id, g, ev_novo, hora_antiga, agora).get("ok"):
                    avisados += 1
            except Exception:  # noqa: BLE001
                import logging
                logging.getLogger(__name__).warning(
                    "aviso de remarcação ao convidado %s falhou", g.get("id"), exc_info=True)
    return {"ok": True, "avisados": avisados, "total_convidados": len(guests)}
