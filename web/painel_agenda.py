"""Aba "Agenda" do painel — calendário do mês, próximos compromissos, novo
compromisso, lembrete (opt-in) e sincronização com outras agendas via .ics.

Agenda PRÓPRIA do Zaq (a mesma que o chat usa em finance/agenda.py). Escopo
multi-tenant sagrado: toda query filtra por conta[0]. O feed .ics é público por
token (rota /agenda/<token>.ics, fora do gate do painel) — o segredo é o token.

Reusa o motor do portal: _render/_env (base, nav) + conta_logada.
"""
import calendar as _cal
from datetime import date, datetime, timedelta
from urllib.parse import quote

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from db.conexao import get_pool
from finance import agenda as ag
from finance import convites as cv
from web.portal import _env, _render, conta_logada

router = APIRouter()

MESES = ["", "janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
         "agosto", "setembro", "outubro", "novembro", "dezembro"]
DIAS_SEM = ["Dom", "Seg", "Ter", "Qua", "Qui", "Sex", "Sáb"]
DIAS_SEM_EXT = ["Domingo", "Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado"]
TIPO_ROT = {"pessoal": "Pessoal", "empresa": "Empresa", "fornecedor": "Fornecedor"}


def _acesso(request: Request):
    """Qualquer membro logado vê a agenda da sua conta."""
    conta = conta_logada(request)
    if conta is None:
        return None, RedirectResponse("/login", status_code=303)
    ctx = {"conta": conta, "conta_id": conta[0],
           "membro_id": request.session.get("membro_id")}
    return ctx, None


def _mes_ref(m: str) -> tuple[int, int]:
    """'2026-07' -> (2026, 7). Inválido/vazio -> mês atual (Brasília)."""
    hoje = ag.agora_brt()
    try:
        ano, mes = (m or "").split("-")
        ano, mes = int(ano), int(mes)
        if 1 <= mes <= 12 and 2000 <= ano <= 2100:
            return ano, mes
    except (ValueError, AttributeError):
        pass
    return hoje.year, hoje.month


def _vizinho(ano: int, mes: int, delta: int) -> str:
    """Mês anterior/seguinte no formato YYYY-MM."""
    idx = (ano * 12 + (mes - 1)) + delta
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def _monta_semanas(ano: int, mes: int, eventos: list[dict], hoje: date) -> list[list[dict]]:
    """Grade do mês (semanas de Dom a Sáb). Cada célula traz seus eventos."""
    por_dia: dict[date, list[dict]] = {}
    for ev in eventos:
        d = ev["inicio"].astimezone(ag.BRT).date()
        por_dia.setdefault(d, []).append(ev)
    cal = _cal.Calendar(firstweekday=6)   # 6 = domingo
    semanas = []
    for semana in cal.monthdatescalendar(ano, mes):
        linha = []
        for d in semana:
            evs = por_dia.get(d, [])
            linha.append({
                "dia": d.day, "fora": d.month != mes, "hoje": d == hoje,
                "iso": d.isoformat(),
                "eventos": [{"id": e["id"], "titulo": e["titulo"], "tipo": e["tipo"],
                             "hora": e["inicio"].astimezone(ag.BRT).strftime("%H:%M")}
                            for e in evs],
            })
        semanas.append(linha)
    return semanas


def _titulo_dia(d: date) -> str:
    """'Terça, 4 de agosto' — pro cabeçalho da caixa do dia."""
    idx = (d.weekday() + 1) % 7   # Calendar usa firstweekday=6 (domingo) -> 0=Dom
    return f"{DIAS_SEM_EXT[idx]}, {d.day} de {MESES[d.month]}"


def _eventos_por_dia(eventos: list[dict], convidados: dict[int, list[dict]] | None = None) -> dict[str, dict]:
    """{iso_do_dia: {titulo, eventos:[...]}} com os detalhes completos (local,
    descrição, convidados) — alimenta a caixa do dia no JS sem precisar de outra
    requisição (os eventos do mês já vieram pro calendário)."""
    convidados = convidados or {}
    out: dict[str, dict] = {}
    for e in sorted(eventos, key=lambda ev: ev["inicio"]):
        d = e["inicio"].astimezone(ag.BRT).date()
        iso = d.isoformat()
        bucket = out.setdefault(iso, {"titulo": _titulo_dia(d), "eventos": []})
        conv_lista = []
        for g in convidados.get(e["id"], []):
            texto = f"Oi{(' ' + g['nome']) if g.get('nome') else ''}! Sobre o compromisso \"{e['titulo']}\"…"
            conv_lista.append({"nome": g.get("nome") or "", "contato": g.get("contato") or "",
                               "status": g["status"], "status_rot": g["status_rot"],
                               "wa": _wa_share(g["contato"], texto) if g.get("contato") else ""})
        bucket["eventos"].append({
            "id": e["id"], "hora": e["inicio"].astimezone(ag.BRT).strftime("%H:%M"),
            "titulo": e["titulo"], "tipo": e["tipo"], "tipo_rot": TIPO_ROT.get(e["tipo"], "Pessoal"),
            "local": e.get("local") or "", "descricao": e.get("descricao") or "",
            "convidados": conv_lista,
        })
    return out


def _feed_url(request: Request, token: str) -> str:
    base = str(request.base_url).rstrip("/")
    return f"{base}/agenda/{token}.ics"


def _convite_url(request: Request, token: str) -> str:
    base = str(request.base_url).rstrip("/")
    return f"{base}/convite/{token}"


def _so_digitos(s: str) -> str:
    return "".join(ch for ch in (s or "") if ch.isdigit())


def _wa_share(contato: str, texto: str) -> str:
    """Link que abre o WhatsApp DA PRÓPRIA pessoa (dono) já com a mensagem pronta
    pro cliente. Se tiver número, manda direto pra ele; senão, abre pra escolher."""
    d = _so_digitos(contato)
    if d and not d.startswith("55") and len(d) <= 11:
        d = "55" + d
    alvo = d if d else ""
    return f"https://wa.me/{alvo}?text={quote(texto)}"


def _montar_share(request: Request, pool, conta_id: int, convite_ev: str, convite: str):
    """Card de compartilhar os convites de UM evento (todos os convidados dele)."""
    ev_id = None
    if convite_ev and convite_ev.isdigit():
        ev_id = int(convite_ev)
    elif convite:                                  # retrocompat: token -> evento dele
        c = cv.por_token(pool, convite)
        if c and c["conta_id"] == conta_id:
            ev_id = c["evento"]["id"]
    if not ev_id:
        return None
    ev = ag.evento_por_id(pool, conta_id, ev_id)
    if not ev:
        return None
    guests = cv.por_evento(pool, conta_id, [ev_id]).get(ev_id, [])
    if not guests:
        return None
    quando = ag.fmt_hora(ev)
    local = ev.get("local")
    nomes = [g["nome"] for g in guests if (g.get("nome") or "").strip()]
    com = f" Com: {ag.frase_nomes(nomes)}." if len(nomes) > 1 else ""
    lista = []
    for g in guests:
        url = _convite_url(request, g["token"])
        msg = (f"Oi{(' ' + g['nome']) if g['nome'] else ''}! Quero marcar uma reunião: "
               f"{ev['titulo']} — {quando}{(' (' + local + ')') if local else ''}.{com} "
               f"Confirma pra mim aqui: {url}")
        lista.append({"nome": g["nome"], "contato": g["contato"], "url": url,
                      "wa": _wa_share(g["contato"] or "", msg), "token": g["token"],
                      "status": g["status"], "status_rot": g["status_rot"]})
    return {"titulo": ev["titulo"], "quando": quando, "guests": lista, "ev_id": ev_id,
            "total": len(lista), "resumo": cv.resumo(guests),
            "auto_on": cv.template_configurado()}


# ================================================================ CALENDÁRIO
@router.get("/painel/agenda", response_class=HTMLResponse)
def agenda_home(request: Request, m: str = "", novo: str = "", convite: str = "",
                convite_ev: str = ""):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    pool = get_pool()
    conta_id = ctx["conta_id"]
    ano, mes = _mes_ref(m)
    hoje = ag.agora_brt().date()
    eventos = ag.eventos_mes(pool, conta_id, ano, mes)
    semanas = _monta_semanas(ano, mes, eventos, hoje)
    proximos = ag.proximos(pool, conta_id, limite=8)
    ids_com_convidados = {e["id"] for e in eventos} | {e["id"] for e in proximos}
    convidados = cv.por_evento(pool, conta_id, list(ids_com_convidados))
    eventos_dia = _eventos_por_dia(eventos, convidados)
    for ev in proximos:
        ev["dia_rot"] = ev["inicio"].astimezone(ag.BRT).strftime("%d/%m")
        ev["hora_rot"] = ev["inicio"].astimezone(ag.BRT).strftime("%H:%M")
        ev["tipo_rot"] = TIPO_ROT.get(ev["tipo"], "Pessoal")
        ev["convidados"] = convidados.get(ev["id"], [])
        ev["conv_resumo"] = cv.resumo(ev["convidados"]) if ev["convidados"] else None
    cfg = ag.get_config(pool, conta_id)
    feed_url = _feed_url(request, cfg["feed_token"]) if cfg.get("feed_token") else ""
    # Card de compartilhar os convites de um evento (?convite_ev=<id>; aceita também
    # ?convite=<token> por retrocompat, resolvendo pro evento dele).
    share = _montar_share(request, pool, conta_id, convite_ev, convite)
    return _render("agenda", request, titulo="Agenda", secao_ativa="agenda",
                   ano=ano, mes=mes, mes_nome=MESES[mes], dias_sem=DIAS_SEM,
                   semanas=semanas, proximos=proximos, tipo_rot=TIPO_ROT,
                   eventos_dia=eventos_dia, status_rot=cv.STATUS_ROT,
                   mes_prev=_vizinho(ano, mes, -1), mes_next=_vizinho(ano, mes, +1),
                   mes_hoje=f"{hoje.year:04d}-{hoje.month:02d}",
                   hoje_iso=hoje.isoformat(), abrir_novo=(novo == "1"),
                   cfg=cfg, feed_url=feed_url, share=share,
                   aviso=request.session.pop("agenda_aviso", None))


# ================================================================ NOVO
@router.post("/painel/agenda/novo")
def agenda_novo(request: Request, titulo: str = Form(...), data: str = Form(""),
                hora: str = Form(""), local: str = Form(""), descricao: str = Form(""),
                tipo: str = Form("pessoal"),
                convidado_nome: list[str] = Form(default=[]),
                convidado_contato: list[str] = Form(default=[]),
                m: str = Form("")):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    titulo = (titulo or "").strip()
    quando = f"{(data or '').strip()} {(hora or '').strip()}".strip()
    inicio = ag.parse_datahora(quando)
    voltar = f"/painel/agenda?m={m}" if m else "/painel/agenda"
    if not titulo:
        request.session["agenda_aviso"] = "Dá um nome pro compromisso."
        return RedirectResponse(voltar + ("&" if "?" in voltar else "?") + "novo=1", status_code=303)
    if not inicio:
        request.session["agenda_aviso"] = "Não entendi a data/hora. Confere aí."
        return RedirectResponse(voltar + ("&" if "?" in voltar else "?") + "novo=1", status_code=303)
    pool = get_pool()
    ev = ag.criar_evento(pool=pool, conta_id=ctx["conta_id"], titulo=titulo,
                         inicio=inicio, membro_id=ctx["membro_id"],
                         local=(local or "").strip() or None,
                         descricao=(descricao or "").strip() or None,
                         tipo=tipo if tipo in ag.TIPOS else "pessoal")
    destino = f"/painel/agenda?m={inicio.year:04d}-{inicio.month:02d}"
    # Convidados (um ou vários): cria um convite por linha preenchida.
    pares = []
    for i in range(max(len(convidado_nome), len(convidado_contato))):
        nm = convidado_nome[i] if i < len(convidado_nome) else ""
        ct = convidado_contato[i] if i < len(convidado_contato) else ""
        if (nm or "").strip() or (ct or "").strip():
            pares.append((nm, ct))
    if pares:
        for nm, ct in pares:
            cv.criar_convidado(pool, ctx["conta_id"], ev["id"], nm, ct)
        n = len(pares)
        request.session["agenda_aviso"] = (
            f"“{titulo}” marcado. Agora é só enviar {'o convite' if n == 1 else f'os {n} convites'}.")
        return RedirectResponse(destino + f"&convite_ev={ev['id']}", status_code=303)
    request.session["agenda_aviso"] = f"“{titulo}” marcado pra {inicio.strftime('%d/%m às %H:%M')}."
    return RedirectResponse(destino, status_code=303)


# ================================================================ BUSCAR LOCAL
@router.get("/painel/agenda/buscar-local")
def agenda_buscar_local(request: Request, q: str = "", lat: str = "", lng: str = ""):
    """Sugestões de endereço/lugar pro campo Local (autocomplete no form de novo
    compromisso). Reusa a mesma Google Places API já usada na prospecção — aqui
    sem cidade/segmento, é busca livre. Devolve endereço FORMATADO pelo Google
    (o que faz o link do mapa depois ser exato, não uma busca por texto solto).

    `lat`/`lng` (opcionais): posição do navegador, quando o usuário permite —
    enviesa o ranking pro entorno, não restringe (long-distance ainda aparece
    se o texto bater)."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "auth", "itens": []}, status_code=401)
    from finance import prospeccao_fontes as pf
    if not pf.tem_chave_places():
        return JSONResponse({"ok": False, "erro": "sem_chave", "itens": []})
    termo = (q or "").strip()
    if len(termo) < 2:
        return JSONResponse({"ok": True, "itens": []})
    lat_f = lng_f = None
    try:
        if lat and lng:
            lat_f, lng_f = float(lat), float(lng)
    except ValueError:
        pass
    r = pf.buscar_places(termo, cidade="", max_resultados=6, lat=lat_f, lng=lng_f)
    if not r.get("ok"):
        return JSONResponse({"ok": False, "erro": r.get("erro"), "itens": []})
    itens = [{"nome": i["empresa"], "endereco": i["endereco"]}
             for i in r["itens"] if i.get("endereco")]
    return JSONResponse({"ok": True, "itens": itens})


# ================================================================ CANCELAR
@router.post("/painel/agenda/cancelar")
def agenda_cancelar(request: Request, evento_id: int = Form(...), m: str = Form("")):
    ajax = bool(request.headers.get("x-zaq-ajax"))
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "auth"}, status_code=401) if ajax else redir
    ok = ag.cancelar_evento(get_pool(), ctx["conta_id"], evento_id)
    if ajax:
        return JSONResponse({"ok": bool(ok)})
    if ok:
        request.session["agenda_aviso"] = "Compromisso cancelado."
    voltar = f"/painel/agenda?m={m}" if m else "/painel/agenda"
    return RedirectResponse(voltar, status_code=303)


_ERRO_ENVIO = {
    "sem_numero": "esse convidado não tem número de WhatsApp",
    "sem_template": "o template ainda não está configurado (falta a aprovação / o SID)",
    "nao_configurado": "o WhatsApp (Twilio) não está configurado",
    "sem_numero_empresa": "esta empresa ainda não tem número de WhatsApp configurado (aba Canais)",
    "provedor_sem_template": "o número desta empresa não é Twilio — o template só sai por número Twilio",
    "numero_invalido": "o número do convidado parece inválido",
    "convite_nao_encontrado": "convite não encontrado",
}


@router.post("/painel/agenda/convite/enviar")
def agenda_convite_enviar(request: Request, token: str = Form(...),
                          ev_id: str = Form(""), m: str = Form("")):
    """Dispara o convite pelo WhatsApp (template) pro número do convidado."""
    ajax = bool(request.headers.get("x-zaq-ajax"))
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "msg": "Faça login."}, status_code=401) if ajax else redir
    pool = get_pool()
    c = cv.por_token(pool, token)
    if not c or c["conta_id"] != ctx["conta_id"]:      # ownership sagrado
        ok, msg = False, "Convite não encontrado."
    elif c["status"] == "confirmado":                  # já confirmou: não reenvia
        ok, msg = False, f"{c['nome'] or 'O convidado'} já confirmou — não precisa reenviar. ✅"
    else:
        r = cv.enviar_convite_whatsapp(pool, token)
        quem = c["nome"] or "convidado"
        if r.get("ok"):
            ok, msg = True, f"Convite enviado pelo WhatsApp pra {quem}! ✅"
        else:
            motivo = _ERRO_ENVIO.get(r.get("erro"), r.get("erro") or "erro desconhecido")
            ok, msg = False, f"Não consegui enviar pra {quem}: {motivo}."
    if ajax:
        return JSONResponse({"ok": ok, "msg": msg})
    request.session["agenda_aviso"] = msg
    destino = f"/painel/agenda?m={m}" if m else "/painel/agenda"
    if ev_id:
        destino += ("&" if "?" in destino else "?") + f"convite_ev={ev_id}"
    return RedirectResponse(destino, status_code=303)


# ================================================================ LEMBRETE (opt-in)
@router.post("/painel/agenda/lembrete")
def agenda_lembrete(request: Request, resumo_dia: str = Form(""),
                    hora_resumo: str = Form("7"), aviso: str = Form(""),
                    aviso_antes_min: str = Form("30"), avisar_convidados: str = Form(""),
                    m: str = Form("")):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    resumo_on = resumo_dia == "1"
    aviso_on = aviso == "1"
    try:
        hora = int(hora_resumo)
    except (TypeError, ValueError):
        hora = 7
    try:
        antes = int(aviso_antes_min) if aviso_on else None
    except (TypeError, ValueError):
        antes = 30 if aviso_on else None
    ag.salvar_config(get_pool(), ctx["conta_id"],
                     resumo_ativo=resumo_on,
                     hora_resumo=hora,
                     aviso_antes_min=antes,
                     avisar_convidados=(avisar_convidados == "1"))
    if resumo_on or aviso_on:
        request.session["agenda_aviso"] = "Lembrete ligado. 🔔"
    else:
        request.session["agenda_aviso"] = "Lembrete desligado."
    voltar = f"/painel/agenda?m={m}" if m else "/painel/agenda"
    return RedirectResponse(voltar, status_code=303)


# ================================================================ SINCRONIZAR (.ics)
@router.post("/painel/agenda/sincronizar")
def agenda_sincronizar(request: Request, m: str = Form("")):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    ag.garantir_feed_token(get_pool(), ctx["conta_id"])
    voltar = f"/painel/agenda?m={m}" if m else "/painel/agenda"
    return RedirectResponse(voltar, status_code=303)


# ---------------------------------------------------------------- feed público .ics
@router.get("/agenda/{token}.ics")
def agenda_feed(token: str):
    """Feed .ics assinável (público por token). Google/Apple/Outlook leem por URL."""
    pool = get_pool()
    conta_id = ag.conta_por_feed_token(pool, token)
    if conta_id is None:
        return Response("not found", status_code=404)
    ics = ag.feed_ics(ag.eventos_para_feed(pool, conta_id))
    return Response(ics, media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": 'inline; filename="zaq.ics"',
                             "Cache-Control": "public, max-age=300"})


# ================================================================ CONVITE (público)
_ACAO_STATUS = {"confirmar": "confirmado", "remarcar": "remarcar", "recusar": "recusado"}


def _render_convite(c: dict, resultado: str | None) -> HTMLResponse:
    """Página pública de confirmação. `resultado` = status recém-registrado (ou None
    pra mostrar os botões). Se o convidado já respondeu antes, mostra o resultado."""
    ev = c["evento"]
    estado = resultado or (c["status"] if c["status"] != "pendente" else None)
    ctx = {
        "empresa": c["empresa"] or "A empresa", "nome": c["nome"] or "",
        "titulo": ev["titulo"], "quando": ag.fmt_hora(c["evento"]),
        "local": ev.get("local") or "", "token": c["token"],
        "estado": estado, "resposta": c.get("resposta") or "",
        "cal_link": cv.link_calendario(ev) if estado == "confirmado" else "",
        "mapa_link": (cv.link_mapa(ev) if estado == "confirmado" else "") or "",
    }
    return HTMLResponse(_env.get_template("convite").render(**ctx))


@router.get("/convite/{token}", response_class=HTMLResponse)
def convite_ver(request: Request, token: str):
    c = cv.por_token(get_pool(), token)
    if not c:
        return HTMLResponse(_env.get_template("convite_404").render(), status_code=404)
    return _render_convite(c, resultado=None)


@router.post("/convite/{token}/responder", response_class=HTMLResponse)
def convite_responder(request: Request, token: str, acao: str = Form(...),
                      resposta: str = Form("")):
    pool = get_pool()
    status = _ACAO_STATUS.get((acao or "").strip())
    if not status:
        return RedirectResponse(f"/convite/{token}", status_code=303)
    c = cv.responder(pool, token, status, resposta)
    if not c:
        return HTMLResponse(_env.get_template("convite_404").render(), status_code=404)
    if c.get("mudou"):         # só avisa quando o status REALMENTE mudou (não repete)
        cv.pos_resposta(pool, c)
    return _render_convite(c, resultado=status)


# ================================================================ TEMPLATE
_CSS = """<style>
.ag-wrap{max-width:1080px;margin:0 auto}
.ag-top{display:flex;align-items:center;justify-content:space-between;gap:14px;flex-wrap:wrap;margin:.2rem 0 1rem}
.ag-mes{display:flex;align-items:center;gap:12px}
.ag-mes h1{font-size:1.32rem;margin:0;text-transform:capitalize;letter-spacing:-.01em}
.ag-nav{display:inline-flex;gap:6px}
.ag-nav a,.ag-hoje{display:inline-flex;align-items:center;justify-content:center;min-width:34px;height:34px;padding:0 10px;border:1px solid var(--borda);border-radius:9px;background:var(--card-2);color:var(--txt);text-decoration:none;font-size:.9rem}
.ag-nav a:hover,.ag-hoje:hover{border-color:var(--verde)}
.ag-hoje{font-size:.82rem}
.ag-btn{width:auto;margin:0;display:inline-flex;align-items:center;gap:7px;background:var(--verde);color:#04160e;font-weight:700;border:0;border-radius:10px;padding:.6rem 1rem;font-size:.92rem;cursor:pointer;text-decoration:none}
/* feedback instantâneo: botão "reagindo" enquanto o back processa */
.is-busy{opacity:.62;cursor:progress!important;pointer-events:none}
/* toast (aviso flutuante) — usado nas ações sem reload */
.zaq-toast{position:fixed;left:50%;bottom:22px;transform:translateX(-50%) translateY(12px);background:var(--card-2);border:1px solid var(--verde);color:var(--txt);padding:.7rem 1.05rem;border-radius:12px;font-size:.9rem;box-shadow:0 10px 34px rgba(0,0,0,.45);opacity:0;transition:transform .28s,opacity .28s;z-index:9999;max-width:92vw;text-align:center}
.zaq-toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
.zaq-toast.err{border-color:#e0574f}
.px-row.saindo{opacity:0;transform:translateX(8px);transition:.22s}
.ag-btn:hover{background:var(--verde-hover)}
.ag-grid{display:grid;grid-template-columns:1.7fr .95fr;gap:18px;align-items:start}
@media(max-width:860px){.ag-grid{grid-template-columns:1fr}}
.ag-card{background:var(--card);border:1px solid var(--borda);border-radius:14px;padding:16px}
.ag-card h2{font-size:.76rem;letter-spacing:.09em;text-transform:uppercase;color:var(--txt-mut);margin:0 0 12px;font-weight:700}
/* calendário */
.cal{border:1px solid var(--borda);border-radius:14px;overflow:hidden;background:var(--card)}
.cal-hd,.cal-wk{display:grid;grid-template-columns:repeat(7,1fr)}
.cal-hd{background:var(--card-2);border-bottom:1px solid var(--borda)}
.cal-hd span{padding:9px 0;text-align:center;font-size:.68rem;letter-spacing:.06em;text-transform:uppercase;color:var(--txt-mut);font-weight:700}
.cal-cell{height:84px;min-width:0;overflow:hidden;border-right:1px solid var(--borda);border-bottom:1px solid var(--borda);padding:6px 7px;display:flex;flex-direction:column;gap:3px}
.cal-cell:nth-child(7n){border-right:0}
.cal-wk:last-child .cal-cell{border-bottom:0}
.cal-cell.fora{background:rgba(255,255,255,.012)}
.cal-cell.fora .cal-num{color:#4a4a4c}
.cal-head{display:flex;align-items:center;justify-content:space-between}
.cal-num{font-size:.8rem;color:var(--txt-mut);font-variant-numeric:tabular-nums;line-height:1}
.cal-cell.hoje .cal-num{background:var(--verde);color:#04160e;font-weight:800;width:20px;height:20px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center}
.cal-count{font-size:.62rem;font-weight:700;color:var(--txt-mut);background:var(--card-2);border:1px solid var(--borda);border-radius:20px;padding:0 6px;line-height:1.4}
.cal-cell.tem-evento{cursor:pointer}
.cal-cell.tem-evento:hover{background:var(--card-2)}
.cal-cell.tem-evento:focus-visible{outline:2px solid var(--verde);outline-offset:-2px}
.evs{display:flex;flex-direction:column;gap:2px;min-width:0}
.ev-line{display:flex;align-items:center;gap:4px;min-width:0}
.ev-line .dot{width:6px;height:6px;border-radius:50%;flex:0 0 6px}
.ev-line .h{font-size:.6rem;font-weight:700;color:var(--txt-mut);flex:0 0 auto;font-variant-numeric:tabular-nums}
.ev-line .n{font-size:.62rem;color:var(--txt);flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ev-more{font-size:.58rem;color:var(--txt-mut);padding-left:10px}
/* próximos */
.px{display:flex;flex-direction:column;gap:2px}
.px-row{display:flex;align-items:flex-start;gap:11px;padding:9px 4px;border-bottom:1px solid var(--borda)}
.px-row:last-child{border-bottom:0}
.px-when{flex:0 0 46px;text-align:center;line-height:1.15}
.px-when .d{font-size:.72rem;color:var(--txt-mut);font-variant-numeric:tabular-nums}
.px-when .h{font-size:.92rem;font-weight:700;font-variant-numeric:tabular-nums}
.px-body{flex:1;min-width:0}
.px-body .tt{font-size:.92rem;font-weight:600}
.px-body .mt{font-size:.74rem;color:var(--txt-mut);margin-top:1px}
.px-dot{width:8px;height:8px;border-radius:50%;margin-top:6px;flex:0 0 8px}
.d-pessoal{background:var(--verde-claro)}.d-empresa{background:#3987e5}.d-fornecedor{background:#e0a33e}
.px-x{width:auto;margin:0;background:none;border:0;color:var(--txt-mut);cursor:pointer;font-size:1rem;line-height:1;padding:2px 4px;border-radius:6px}
.px-x:hover{color:#f0917f;background:rgba(224,87,79,.12)}
.px-vazio{color:var(--txt-mut);font-size:.86rem;padding:8px 2px}
/* formulários / cards laterais */
.side-cards{display:flex;flex-direction:column;gap:18px}
.frm label{display:block;font-size:.74rem;color:var(--txt-mut);margin:10px 0 4px;font-weight:600}
.frm input,.frm select,.frm textarea{width:100%;background:var(--card-2);border:1px solid var(--borda);border-radius:9px;color:var(--txt);padding:.55rem .6rem;font-size:.92rem;font-family:inherit}
.frm textarea{resize:vertical;min-height:58px;line-height:1.4}
.frm input:focus,.frm select:focus,.frm textarea:focus{outline:0;border-color:var(--verde)}
/* busca de endereço (campo Local) */
.addr-wrap{position:relative}
.addr-input-row{position:relative}
.addr-input-row input{padding-right:34px}
.addr-ic{position:absolute;right:10px;top:50%;transform:translateY(-50%);font-size:.9rem;color:var(--txt-mut);pointer-events:none}
.addr-drop{position:absolute;left:0;right:0;top:calc(100% + 4px);background:var(--card);border:1px solid var(--borda);border-radius:10px;box-shadow:0 16px 40px rgba(0,0,0,.5);z-index:12;overflow:hidden;display:none}
.addr-drop.show{display:block}
.addr-opt{display:flex;gap:10px;align-items:flex-start;padding:9px 11px;cursor:pointer;border-bottom:1px solid var(--borda)}
.addr-opt:last-child{border-bottom:0}
.addr-opt:hover{background:var(--card-2)}
.addr-opt .pin{flex:0 0 auto;font-size:.85rem;margin-top:1px;color:var(--verde-claro)}
.addr-opt .tt{font-size:.85rem;font-weight:600}
.addr-opt .ad{font-size:.72rem;color:var(--txt-mut);margin-top:1px}
.addr-empty{padding:10px 11px;font-size:.78rem;color:var(--txt-mut)}
.addr-picked{display:flex;align-items:flex-start;gap:9px;background:rgba(29,158,117,.1);border:1px solid var(--verde);border-radius:9px;padding:9px 10px;margin-top:6px;flex-wrap:wrap}
.addr-picked .chk{flex:0 0 auto;color:var(--verde-claro);font-size:.95rem;margin-top:1px}
.addr-picked .body{flex:1;min-width:160px}
.addr-picked .nm{font-size:.86rem;font-weight:700}
.addr-picked .ed{font-size:.74rem;color:var(--txt-mut);margin-top:1px}
.addr-picked .x{flex:0 0 auto;background:transparent;border:0;color:var(--txt-mut);cursor:pointer;font-size:.9rem;padding:2px}
.addr-picked .x:hover{color:#f0917f}
.addr-actions{width:100%;display:flex;justify-content:flex-end;margin-top:2px;position:relative}
.send-btn{display:inline-flex;align-items:center;gap:6px;background:#25d366;color:#04160e;border:0;border-radius:8px;padding:.36rem .65rem;font-size:.76rem;font-weight:700;cursor:pointer}
.send-btn:hover{background:#2ee578}
.hint-line{font-size:.7rem;color:var(--txt-mut);margin-top:6px;min-height:1em}
.manual-toggle{display:inline-flex;align-items:center;gap:5px;background:transparent;border:0;color:var(--txt-mut);cursor:pointer;font-size:.74rem;padding:5px 0;margin-top:2px;text-decoration:underline;text-decoration-color:var(--borda);text-underline-offset:3px}
.manual-toggle:hover{color:var(--verde-claro);text-decoration-color:var(--verde-claro)}
.manual-box{display:none;margin-top:6px;padding-top:10px;border-top:1px dashed var(--borda)}
.manual-box.show{display:block}
.manual-box .mlabel{font-size:.72rem;color:var(--txt-mut);margin-bottom:5px}
.manual-cancel{display:inline-flex;align-items:center;gap:4px;background:transparent;border:0;color:var(--txt-mut);cursor:pointer;font-size:.72rem;padding:6px 0 0;text-decoration:underline;text-decoration-color:var(--borda);text-underline-offset:3px}
.manual-cancel:hover{color:var(--verde-claro);text-decoration-color:var(--verde-claro)}
.local-alt-row{display:flex;flex-wrap:wrap;gap:2px 16px}
.online-toggle{display:inline-flex;align-items:center;gap:5px;background:transparent;border:0;color:var(--txt-mut);cursor:pointer;font-size:.74rem;padding:5px 0;margin-top:2px;text-decoration:underline;text-decoration-color:var(--borda);text-underline-offset:3px}
.online-toggle:hover{color:var(--verde-claro);text-decoration-color:var(--verde-claro)}
.online-box{display:none;margin-top:6px;padding-top:10px;border-top:1px dashed var(--borda)}
.online-box.show{display:block}
.online-box .omsg{font-size:.78rem;color:var(--verde-claro);background:rgba(29,158,117,.1);border:1px solid rgba(29,158,117,.3);border-radius:8px;padding:8px 10px}
.pop{position:absolute;top:calc(100% + 8px);right:0;width:270px;background:var(--card);border:1px solid var(--verde);border-radius:12px;padding:13px;box-shadow:0 20px 50px rgba(0,0,0,.5);z-index:20;display:none}
.pop.show{display:block}
.pop:before{content:"";position:absolute;top:-6px;right:16px;width:11px;height:11px;background:var(--card);border-left:1px solid var(--verde);border-top:1px solid var(--verde);transform:rotate(45deg)}
.pop h4{margin:0 0 8px;font-size:.8rem;color:var(--verde-claro)}
.pop-msg{background:var(--card-2);border:1px solid var(--borda);border-radius:9px;padding:8px 9px;font-size:.73rem;color:var(--txt-mut);line-height:1.55;margin-bottom:10px;white-space:pre-wrap}
.pop-msg b{color:var(--txt)}
.pop-go{display:flex;align-items:center;justify-content:center;gap:7px;width:100%;background:#25d366;color:#04160e;border:0;border-radius:9px;padding:.5rem;font-weight:700;font-size:.8rem;cursor:pointer;text-decoration:none}
.pop-go:hover{background:#2ee578}
.frm .row2{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.frm .segs{display:flex;gap:6px;margin-top:4px}
.frm .segs label{flex:1;margin:0}
.frm .segs input{position:absolute;opacity:0;pointer-events:none}
.frm .segs span{display:block;text-align:center;border:1px solid var(--borda);border-radius:8px;padding:.5rem 0;font-size:.82rem;color:var(--txt-mut);cursor:pointer}
.frm .segs input:checked+span{border-width:2px;font-weight:700;color:var(--txt)}
.frm .segs .s-pessoal input:checked+span{border-color:var(--verde-claro)}
.frm .segs .s-empresa input:checked+span{border-color:#3987e5}
.frm .segs .s-fornecedor input:checked+span{border-color:#e0a33e}
.frm button.ok{margin-top:14px;width:100%;background:var(--verde);color:#04160e;font-weight:700;border:0;border-radius:10px;padding:.62rem;font-size:.92rem;cursor:pointer}
.frm button.ok:hover{background:var(--verde-hover)}
/* toggles do lembrete */
.tg{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:10px 0;border-bottom:1px solid var(--borda)}
.tg:last-of-type{border-bottom:0}
.tg .tg-t{font-size:.9rem}
.tg .tg-s{font-size:.72rem;color:var(--txt-mut);margin-top:1px}
.sw{position:relative;width:42px;height:24px;flex:0 0 42px}
.sw input{position:absolute;opacity:0;width:100%;height:100%;margin:0;cursor:pointer;z-index:2}
.sw .track{position:absolute;inset:0;background:var(--card-2);border:1px solid var(--borda);border-radius:12px;transition:.18s}
.sw .knob{position:absolute;top:3px;left:3px;width:18px;height:18px;background:#8a8a86;border-radius:50%;transition:.18s}
.sw input:checked~.track{background:rgba(29,158,117,.32);border-color:var(--verde)}
.sw input:checked~.knob{left:21px;background:var(--verde-claro)}
.sub-opt{padding:8px 0 2px;display:flex;align-items:center;gap:8px;font-size:.82rem;color:var(--txt-mut)}
.sub-opt select,.sub-opt input{width:auto;background:var(--card-2);border:1px solid var(--borda);border-radius:8px;color:var(--txt);padding:.35rem .5rem;font-size:.84rem;font-family:inherit}
.canal-tag{display:inline-flex;align-items:center;gap:5px;font-size:.76rem;color:var(--txt-mut);margin-top:10px}
/* sincronizar */
.feed{display:flex;align-items:center;gap:8px;margin:2px 0 10px}
.feed input{flex:1;min-width:0;margin:0;background:var(--card-2);border:1px solid var(--borda);border-radius:9px;color:var(--txt);padding:.5rem .6rem;font-size:.8rem;font-family:ui-monospace,monospace}
.feed button{width:auto;margin:0;flex:0 0 auto;background:var(--card-2);border:1px solid var(--borda);border-radius:9px;color:var(--txt);padding:.5rem 14px;cursor:pointer;font-size:.84rem}
.feed button:hover{border-color:var(--verde)}
.sync-steps{list-style:none;padding:0;margin:8px 0 0;display:flex;flex-direction:column;gap:9px}
.sync-steps li{font-size:.82rem;color:var(--txt-mut);line-height:1.4}
.sync-steps b{color:var(--txt)}
.ag-aviso{background:rgba(29,158,117,.14);border:1px solid var(--verde);color:#bfeeda;border-radius:10px;padding:.6rem .8rem;font-size:.88rem;margin-bottom:14px}
.hint{font-size:.78rem;color:var(--txt-mut);margin-top:8px;line-height:1.45}
/* card de compartilhar convite */
.share{background:linear-gradient(180deg,rgba(29,158,117,.10),var(--card));border:1px solid var(--verde);border-radius:14px;padding:16px 18px;margin-bottom:18px}
.share h2{font-size:.95rem;margin:0 0 4px;color:var(--txt)}
.share p{font-size:.86rem;color:var(--txt-mut);margin:0 0 12px}
.share p b{color:var(--txt)}
.share-list{display:flex;flex-direction:column;gap:8px}
.share-row{display:flex;align-items:center;gap:10px;background:var(--card-2);border:1px solid var(--borda);border-radius:10px;padding:8px 10px}
.sr-av{width:30px;height:30px;border-radius:50%;background:#333;display:flex;align-items:center;justify-content:center;font-size:.82rem;font-weight:700;flex:0 0 30px}
.sr-who{flex:1;min-width:0}
.sr-who b{font-size:.9rem;display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sr-who small{font-size:.72rem;color:var(--txt-mut)}
.sr-wa{background:#25d366;color:#04160e;border:0;border-radius:8px;padding:.42rem .7rem;font-size:.8rem;font-weight:700;cursor:pointer;text-decoration:none;white-space:nowrap}
.sr-wa:hover{background:#2ee578}
.sr-za{width:auto;margin:0;background:var(--verde);color:#04160e;border:0;border-radius:8px;padding:.42rem .7rem;font-size:.8rem;font-weight:700;cursor:pointer;white-space:nowrap}
.sr-za:hover{background:var(--verde-hover)}
.sr-za:disabled{background:var(--card-2);color:var(--txt-mut);border:1px solid var(--borda);cursor:default;opacity:.6}
.sr-za:disabled:hover{background:var(--card-2)}
.share-row form{margin:0;flex:0 0 auto}
.sr-cp{width:auto;margin:0;background:var(--card);border:1px solid var(--borda);color:var(--txt);border-radius:8px;padding:.42rem .55rem;cursor:pointer;font-size:.86rem}
.sr-cp:hover{border-color:var(--verde)}
/* convidado / bloco no form */
.gconv{margin-top:14px;padding-top:14px;border-top:1px dashed var(--borda)}
.gconv .gt{font-size:.82rem;color:var(--verde-claro);font-weight:700}
.gconv .gd{font-size:.74rem;color:var(--txt-mut);margin:1px 0 8px}
.guest-row{display:grid;grid-template-columns:1fr 1fr 42px;gap:8px;align-items:stretch;margin-bottom:8px}
.guest-row>div{min-width:0}
.guest-row input{margin:0}
.guest-row .g-rm{margin:0;padding:0;height:auto;display:flex;align-items:center;justify-content:center;background:transparent;border:1px solid var(--borda);color:var(--txt-mut);border-radius:9px;cursor:pointer;font-size:1rem;line-height:1}
.guest-row .g-rm:hover{border-color:#e0574f;color:#f0917f}
.g-add{margin-top:2px;background:transparent;border:1px dashed var(--borda);color:var(--verde-claro);border-radius:9px;padding:.5rem;width:100%;cursor:pointer;font-weight:600;font-size:.84rem}
.g-add:hover{border-color:var(--verde)}
/* status de convidados nos próximos */
.px-conv{display:flex;flex-wrap:wrap;gap:5px;margin-top:5px}
.cgrp{font-size:.7rem;font-weight:700;padding:2px 9px;border-radius:20px;white-space:nowrap;background:rgba(57,135,229,.14);color:#bcd8f6;border:1px solid rgba(57,135,229,.4)}
.cgrp-ok{background:rgba(29,158,117,.16);color:var(--verde-claro);border-color:var(--verde)}
.cpill{font-size:.68rem;font-weight:700;padding:2px 8px;border-radius:20px;white-space:nowrap;display:inline-flex;align-items:center;gap:4px}
.cp-pendente{background:rgba(224,163,62,.14);color:var(--ambar);border:1px solid rgba(224,163,62,.4)}
.cp-confirmado{background:rgba(29,158,117,.16);color:var(--verde-claro);border:1px solid var(--verde)}
.cp-remarcar{background:rgba(57,135,229,.14);color:#bcd8f6;border:1px solid rgba(57,135,229,.4)}
.cp-recusado{background:rgba(224,87,79,.14);color:#f0917f;border:1px solid rgba(224,87,79,.4)}
/* caixa do dia (clique numa célula do calendário) */
.day-overlay{position:fixed;inset:0;background:rgba(4,6,5,.6);display:flex;align-items:center;justify-content:center;padding:24px;z-index:60;opacity:0;pointer-events:none;transition:opacity .16s}
.day-overlay.show{opacity:1;pointer-events:auto}
.daybox{width:100%;max-width:420px;max-height:82vh;overflow-y:auto;background:var(--card);border:1px solid var(--borda);border-radius:16px;padding:20px;transform:translateY(8px);transition:transform .16s;box-shadow:0 24px 60px rgba(0,0,0,.5)}
.day-overlay.show .daybox{transform:translateY(0)}
.daybox-hd{display:flex;align-items:flex-start;justify-content:space-between;gap:10px;margin-bottom:4px}
.daybox-hd h3{font-size:1.02rem;margin:0;letter-spacing:-.005em;text-transform:capitalize;font-weight:700}
.daybox-hd .x{background:transparent;border:1px solid var(--borda);color:var(--txt-mut);width:28px;height:28px;border-radius:8px;cursor:pointer;font-size:.92rem;flex:0 0 28px}
.daybox-hd .x:hover{border-color:var(--verde);color:var(--txt)}
.daybox-sub{font-size:.78rem;color:var(--txt-mut);margin:0 0 14px}
.dev{display:flex;gap:11px;padding:10px 0;border-bottom:1px solid var(--borda)}
.dev:last-of-type{border-bottom:0;padding-bottom:2px}
.dev-dot{width:8px;height:8px;border-radius:50%;margin-top:6px;flex:0 0 8px}
.dev-body{flex:1;min-width:0}
.dev-hora{font-size:.72rem;color:var(--txt-mut);font-variant-numeric:tabular-nums;font-weight:700}
.dev-tt{font-size:.92rem;font-weight:600;margin-top:1px}
.dev-meta{display:flex;flex-wrap:wrap;gap:8px;font-size:.76rem;color:var(--txt-mut);margin-top:4px}
.dev-desc{font-size:.8rem;color:var(--txt-mut);margin-top:6px;line-height:1.45;background:var(--card-2);border:1px solid var(--borda);border-radius:8px;padding:7px 9px}
.dev-conv{margin-top:9px;padding-top:9px;border-top:1px dashed var(--borda)}
.dev-conv-lbl{font-size:.66rem;font-weight:700;letter-spacing:.04em;text-transform:uppercase;color:var(--txt-mut);margin-bottom:6px}
.guest{display:flex;align-items:center;gap:8px;padding:5px 0}
.guest-info{flex:1;min-width:0}
.guest-nome{font-size:.85rem;font-weight:600;color:var(--txt);display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.guest-fone{font-size:.74rem;color:var(--txt-mut);font-variant-numeric:tabular-nums;margin-top:1px}
.guest-wa{flex:0 0 auto;width:30px;height:30px;border-radius:8px;background:#25d366;color:#04160e;border:0;display:flex;align-items:center;justify-content:center;font-size:.92rem;text-decoration:none;cursor:pointer}
.guest-wa:hover{background:#2ee578}
.tpill{font-size:.64rem;font-weight:700;padding:1px 7px;border-radius:20px;text-transform:uppercase;letter-spacing:.03em}
.tp-pessoal{background:rgba(29,158,117,.16);color:var(--verde-claro)}
.tp-empresa{background:rgba(57,135,229,.16);color:#bcd8f6}
.tp-fornecedor{background:rgba(224,163,62,.16);color:#f0d9a6}
.daybox-cta{display:block;width:100%;text-align:center;margin-top:14px;background:transparent;border:1px dashed var(--borda);color:var(--verde-claro);border-radius:9px;padding:.6rem;font-size:.82rem;font-weight:600;cursor:pointer}
.daybox-cta:hover{border-color:var(--verde)}
</style>"""

_AGENDA_TPL = """{% extends "base" %}{% block conteudo %}""" + _CSS + """
<div class="ag-wrap">
  {% if aviso %}<div class="ag-aviso">{{ aviso }}</div>{% endif %}
  {% if share %}
  <div class="share">
    <h2>✅ {{ share.total }} convite{{ 's' if share.total != 1 }} pronto{{ 's' if share.total != 1 }} pra enviar</h2>
    <p><b>{{ share.titulo }}</b> — {{ share.quando }}. {% if share.auto_on %}Toque em <b>📲 Zaq</b> pra ele mandar o convite sozinho pelo WhatsApp (a pessoa confirma num toque), ou use o link.{% else %}Mande o link de cada um; cada pessoa confirma o seu e você é avisado aqui.{% endif %}</p>
    <div class="share-list">
      {% for g in share.guests %}
      <div class="share-row" data-link="{{ g.url }}">
        <div class="sr-av">{{ (g.nome or '?')[0]|upper }}</div>
        <div class="sr-who"><b>{{ g.nome or 'Convidado' }}</b><small>{{ g.contato or 'sem número' }} · {{ g.status_rot }}</small></div>
        {% if share.auto_on and g.contato %}
          {% if g.status == 'confirmado' %}
          <button type="button" class="sr-za" disabled title="Já confirmou — não precisa reenviar">📲 Zaq</button>
          {% else %}
          <form method="post" action="/painel/agenda/convite/enviar" data-ajax="enviar" style="margin:0">
            <input type="hidden" name="token" value="{{ g.token }}">
            <input type="hidden" name="ev_id" value="{{ share.ev_id }}">
            <input type="hidden" name="m" value="{{ '%04d-%02d'|format(ano, mes) }}">
            <button type="submit" class="sr-za" data-busy="⏳ Enviando…" title="O Zaq envia o convite pelo WhatsApp">📲 Zaq</button>
          </form>
          {% endif %}
        {% endif %}
        <a class="sr-wa" href="{{ g.wa }}" target="_blank" rel="noopener">💬 Enviar</a>
        <button type="button" class="sr-cp" onclick="cpRow(this)" title="Copiar link">📋</button>
      </div>
      {% endfor %}
    </div>
  </div>
  {% endif %}
  <div class="ag-top">
    <div class="ag-mes">
      <div class="ag-nav">
        <a href="/painel/agenda?m={{ mes_prev }}" aria-label="Mês anterior">‹</a>
        <a href="/painel/agenda?m={{ mes_next }}" aria-label="Próximo mês">›</a>
      </div>
      <h1>{{ mes_nome }} de {{ ano }}</h1>
      <a href="/painel/agenda?m={{ mes_hoje }}" class="ag-hoje">Hoje</a>
    </div>
    <a href="#novo" class="ag-btn" onclick="agNovo(true)">＋ Novo compromisso</a>
  </div>

  <div class="ag-grid">
    <div>
      <div class="cal">
        <div class="cal-hd">{% for d in dias_sem %}<span>{{ d }}</span>{% endfor %}</div>
        {% for semana in semanas %}
        <div class="cal-wk">
          {% for c in semana %}
          <div class="cal-cell{% if c.fora %} fora{% endif %}{% if c.hoje %} hoje{% endif %}{% if c.eventos %} tem-evento{% endif %}"
               {% if c.eventos %}data-iso="{{ c.iso }}" tabindex="0" role="button" aria-label="Ver os {{ c.eventos|length }} compromisso{{ 's' if c.eventos|length != 1 }} do dia {{ c.dia }}"{% endif %}>
            <div class="cal-head">
              <span class="cal-num">{{ c.dia }}</span>
              {% if c.eventos|length > 2 %}<span class="cal-count">{{ c.eventos|length }}</span>{% endif %}
            </div>
            {% if c.eventos %}
            <div class="evs">
              {% for e in c.eventos[:2] %}
              <div class="ev-line" data-ev="{{ e.id }}"><span class="dot d-{{ e.tipo }}"></span><span class="h">{{ e.hora }}</span><span class="n">{{ e.titulo }}</span></div>
              {% endfor %}
            </div>
            {% if c.eventos|length > 2 %}<div class="ev-more">+{{ c.eventos|length - 2 }} mais</div>{% endif %}
            {% endif %}
          </div>
          {% endfor %}
        </div>
        {% endfor %}
      </div>
      <p class="hint">As cores separam <b style="color:#bfeeda">pessoal</b>, <b style="color:#bcd8f6">empresa</b> e <b style="color:#f0d9a6">fornecedor</b>. Marque também pelo WhatsApp/Telegram — cai tudo aqui.</p>
    </div>

    <div class="side-cards">
      <!-- próximos -->
      <div class="ag-card">
        <h2>Próximos compromissos</h2>
        <div class="px">
          {% for e in proximos %}
          <div class="px-row" data-ev="{{ e.id }}">
            <div class="px-dot d-{{ e.tipo }}"></div>
            <div class="px-when"><div class="d">{{ e.dia_rot }}</div><div class="h">{{ e.hora_rot }}</div></div>
            <div class="px-body">
              <div class="tt">{{ e.titulo }}</div>
              <div class="mt">{{ e.tipo_rot }}{% if e.local %} · {{ e.local }}{% endif %}</div>
              {% if e.convidados %}
              <div class="px-conv">
                {% if e.conv_resumo.total > 1 %}
                <a href="/painel/agenda?m={{ '%04d-%02d'|format(ano, mes) }}&convite_ev={{ e.id }}" class="cgrp{% if e.conv_resumo.fechado %} cgrp-ok{% endif %}" style="text-decoration:none" title="Ver e reenviar os convites">👥 {{ e.conv_resumo.confirmados }} de {{ e.conv_resumo.total }} confirmaram{% if e.conv_resumo.fechado %} 🎉{% endif %}</a>
                {% else %}
                {% for g in e.convidados %}<a href="/painel/agenda?m={{ '%04d-%02d'|format(ano, mes) }}&convite_ev={{ e.id }}" class="cpill cp-{{ g.status }}" style="text-decoration:none" title="Reenviar o link do convite de {{ g.nome or 'convidado' }}">👤 {{ g.nome or 'Convidado' }}: {{ g.status_rot }}</a>{% endfor %}
                {% endif %}
              </div>
              {% endif %}
            </div>
            <form method="post" action="/painel/agenda/cancelar" data-ajax="cancelar" onsubmit="return confirm('Cancelar “{{ e.titulo }}”?')">
              <input type="hidden" name="evento_id" value="{{ e.id }}">
              <input type="hidden" name="m" value="{{ '%04d-%02d'|format(ano, mes) }}">
              <button class="px-x" type="submit" title="Cancelar">✕</button>
            </form>
          </div>
          {% else %}
          <div class="px-vazio">Nada por vir. Marque um compromisso ali em cima. 🎉</div>
          {% endfor %}
        </div>
      </div>

      <!-- novo compromisso -->
      <div class="ag-card" id="novo"{% if not abrir_novo %} style="display:none"{% endif %}>
        <h2>Novo compromisso</h2>
        <form class="frm" method="post" action="/painel/agenda/novo">
          <input type="hidden" name="m" value="{{ '%04d-%02d'|format(ano, mes) }}">
          <label>O que é</label>
          <input name="titulo" id="fTitulo" placeholder="Ex: reunião com o contador" required autocomplete="off">
          <div class="row2">
            <div><label>Data</label><input name="data" id="fData" type="date" value="{{ hoje_iso }}" required></div>
            <div><label>Hora</label><input name="hora" id="fHora" type="time" value="09:00"></div>
          </div>
          <label>Descrição <span style="font-weight:400">(opcional)</span></label>
          <textarea name="descricao" placeholder="Detalhes do compromisso…"></textarea>
          <label>Local <span style="font-weight:400">(opcional)</span></label>
          <input type="hidden" name="local" id="localHidden">
          <div class="addr-wrap" id="addrWrap">
            <div class="addr-input-row">
              <input type="text" id="addrInput" placeholder="Buscar endereço ou nome do lugar…" autocomplete="off">
              <span class="addr-ic">🔍</span>
            </div>
            <div class="addr-drop" id="addrDrop"></div>
            <div id="addrPicked"></div>
          </div>
          <div class="hint-line" id="hintLine">Digite pra buscar — ex: nome do lugar ou endereço.</div>
          <div class="local-alt-row">
            <button type="button" class="manual-toggle" id="manualToggle">✍️ Não achei o lugar — digitar manualmente</button>
            <button type="button" class="online-toggle" id="onlineToggle">🌐 É uma reunião online</button>
          </div>
          <div class="manual-box" id="manualBox">
            <div class="mlabel">Local (texto livre, sem link de mapa)</div>
            <input type="text" id="manualInput" placeholder="Ex: na casa da Ana, no clube…" autocomplete="off">
            <button type="button" class="manual-cancel" id="manualCancel">← voltar pra busca de endereço</button>
          </div>
          <div class="online-box" id="onlineBox">
            <div class="omsg">🌐 Reunião online — nenhum endereço vai ser enviado aos convidados.</div>
            <button type="button" class="manual-cancel" id="onlineCancel">← voltar a informar um local</button>
          </div>
          <label>Tipo</label>
          <div class="segs">
            <label class="s-pessoal"><input type="radio" name="tipo" value="pessoal" checked><span>Pessoal</span></label>
            <label class="s-empresa"><input type="radio" name="tipo" value="empresa"><span>Empresa</span></label>
            <label class="s-fornecedor"><input type="radio" name="tipo" value="fornecedor"><span>Fornecedor</span></label>
          </div>
          <div class="gconv">
            <div class="gt">👥 Envolvidos (opcional)</div>
            <div class="gd">Um ou vários. Cada um recebe o próprio link — e a mensagem já cita quem mais vem.</div>
            <div id="guests">
              <div class="guest-row">
                <div><input class="gnome" name="convidado_nome" placeholder="Nome" autocomplete="off"></div>
                <div><input name="convidado_contato" placeholder="(86) 90000-0000" autocomplete="off"></div>
                <button type="button" class="g-rm" onclick="rmGuest(this)" title="Remover" aria-label="Remover">✕</button>
              </div>
            </div>
            <button type="button" class="g-add" onclick="addGuest()">+ adicionar envolvido</button>
          </div>
          <button class="ok" type="submit" data-busy="⏳ Marcando…">Marcar</button>
        </form>
      </div>

      <!-- lembrete -->
      <div class="ag-card">
        <h2>🔔 Lembrete</h2>
        <form method="post" action="/painel/agenda/lembrete">
          <input type="hidden" name="m" value="{{ '%04d-%02d'|format(ano, mes) }}">
          <div class="tg">
            <div><div class="tg-t">Resumo do dia</div><div class="tg-s">De manhã, o que você tem no dia</div></div>
            <label class="sw"><input type="checkbox" name="resumo_dia" value="1" {% if cfg.resumo_ativo %}checked{% endif %} onchange="document.getElementById('subResumo').style.display=this.checked?'flex':'none'"><span class="track"></span><span class="knob"></span></label>
          </div>
          <div class="sub-opt" id="subResumo"{% if not cfg.resumo_ativo %} style="display:none"{% endif %}>
            <span>às</span>
            <select name="hora_resumo">
              {% for h in range(5,12) %}<option value="{{ h }}" {% if cfg.hora_resumo==h %}selected{% endif %}>{{ '%02d:00'|format(h) }}</option>{% endfor %}
            </select>
          </div>
          <div class="tg">
            <div><div class="tg-t">Aviso antes do compromisso</div><div class="tg-s">Um toque minutos antes de cada um</div></div>
            <label class="sw"><input type="checkbox" name="aviso" value="1" {% if cfg.aviso_antes_min %}checked{% endif %} onchange="document.getElementById('subAviso').style.display=this.checked?'block':'none'"><span class="track"></span><span class="knob"></span></label>
          </div>
          <div id="subAviso"{% if not cfg.aviso_antes_min %} style="display:none"{% endif %}>
            <div class="sub-opt">
              <select name="aviso_antes_min">
                {% for mn in [10,15,30,60,120] %}<option value="{{ mn }}" {% if cfg.aviso_antes_min==mn %}selected{% endif %}>{{ mn }} min antes</option>{% endfor %}
              </select>
            </div>
            <div class="tg">
              <div><div class="tg-t">Avisar os convidados</div><div class="tg-s">Quem confirmou presença recebe o mesmo aviso, pelo WhatsApp</div></div>
              <label class="sw"><input type="checkbox" name="avisar_convidados" value="1" {% if cfg.avisar_convidados %}checked{% endif %}><span class="track"></span><span class="knob"></span></label>
            </div>
          </div>
          <div class="canal-tag">📲 Vai chegar no seu WhatsApp/Telegram, onde você fala com o Zaq.</div>
          <button class="ok" type="submit" data-busy="⏳ Salvando…">Salvar lembrete</button>
        </form>
      </div>

      <!-- sincronizar -->
      <div class="ag-card">
        <h2>🔗 Sincronizar com sua agenda</h2>
        {% if feed_url %}
        <p class="hint" style="margin-top:0">Cole este link uma vez no seu calendário — ele puxa seus compromissos sozinho.</p>
        <div class="feed">
          <input id="feedUrl" value="{{ feed_url }}" readonly onclick="this.select()">
          <button type="button" onclick="agCopiar()">Copiar</button>
        </div>
        <ul class="sync-steps">
          <li><b>Google Agenda:</b> Outras agendas › + › <b>De um URL</b> › cole o link.</li>
          <li><b>iPhone (Apple):</b> Ajustes › Calendário › Contas › Adicionar › <b>Outro</b> › Assinar calendário.</li>
          <li><b>Outlook:</b> Adicionar calendário › <b>Assinar da Web</b> › cole o link.</li>
        </ul>
        {% else %}
        <p class="hint" style="margin-top:0">Gere um link seguro pra abrir seus compromissos no Google Agenda, Apple ou Outlook — sem senha, sem login.</p>
        <form method="post" action="/painel/agenda/sincronizar">
          <input type="hidden" name="m" value="{{ '%04d-%02d'|format(ano, mes) }}">
          <button class="ag-btn" type="submit" style="margin-top:6px" data-busy="⏳ Ativando…">Ativar sincronização</button>
        </form>
        {% endif %}
      </div>
    </div>
  </div>

  <!-- caixa do dia (abre ao clicar numa célula com compromisso) -->
  <div class="day-overlay" id="dayOverlay">
    <div class="daybox" id="daybox"></div>
  </div>
</div>
<script>
var EVENTOS_DIA = {{ eventos_dia|tojson }};
var TPILL = {pessoal:'Pessoal', empresa:'Empresa', fornecedor:'Fornecedor'};

function _esc(s){ return (s||'').replace(/[&<>"']/g, function(ch){
  return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]; }); }
function abrirDia(iso){
  var d = EVENTOS_DIA[iso];
  if(!d) return;
  var box = document.getElementById('daybox');
  var html = '<div class="daybox-hd"><h3>'+d.titulo+'</h3>'
    + '<button class="x" type="button" onclick="fecharDia()" aria-label="Fechar">✕</button></div>'
    + '<p class="daybox-sub">'+d.eventos.length+(d.eventos.length===1?' compromisso':' compromissos')+'</p>';
  d.eventos.forEach(function(e){
    var conv = '';
    (e.convidados||[]).forEach(function(g){
      conv += '<div class="guest"><div class="guest-info">'
        + '<div class="guest-nome">'+_esc(g.nome||'Convidado')+' <span class="cpill cp-'+g.status+'">'+_esc(g.status_rot)+'</span></div>'
        + '<div class="guest-fone">'+(g.contato?_esc(g.contato):'<i>sem número</i>')+'</div></div>'
        + (g.wa?'<a class="guest-wa" href="'+g.wa+'" target="_blank" rel="noopener" title="Chamar '+_esc(g.nome||'convidado')+' no WhatsApp">💬</a>':'')
        + '</div>';
    });
    if(conv) conv = '<div class="dev-conv"><div class="dev-conv-lbl">👤 Convidados</div>'+conv+'</div>';
    html += '<div class="dev" data-ev="'+e.id+'"><div class="dev-dot d-'+e.tipo+'"></div><div class="dev-body">'
      + '<div class="dev-hora">'+e.hora+'</div>'
      + '<div class="dev-tt">'+e.titulo+'</div>'
      + '<div class="dev-meta"><span class="tpill tp-'+e.tipo+'">'+(TPILL[e.tipo]||e.tipo_rot)+'</span>'
      + (e.local?'<span>📍 '+e.local+'</span>':'')+'</div>'
      + (e.descricao?'<div class="dev-desc">'+e.descricao+'</div>':'')
      + conv
      + '</div></div>';
  });
  html += '<button class="daybox-cta" type="button" onclick="agNovoNoDia(\\''+iso+'\\')">＋ Marcar novo compromisso nesse dia</button>';
  box.innerHTML = html;
  document.getElementById('dayOverlay').classList.add('show');
}
function fecharDia(){ document.getElementById('dayOverlay').classList.remove('show'); }
// Reconstrói o conteúdo de UMA célula (linhas de compromisso + "+N mais") a partir
// de EVENTOS_DIA — mesma fonte de dados da caixa do dia, pra nunca desalinhar.
function renderizarCelula(iso){
  var cel = document.querySelector('.cal-cell[data-iso="'+iso+'"]');
  if(!cel) return;
  var evs = (EVENTOS_DIA[iso] || {}).eventos || [];
  var num = cel.querySelector('.cal-num');
  var numHtml = num ? num.outerHTML : '';
  if(!evs.length){
    cel.classList.remove('tem-evento');
    cel.removeAttribute('tabindex'); cel.removeAttribute('role'); cel.removeAttribute('data-iso');
    cel.innerHTML = '<div class="cal-head">'+numHtml+'</div>';
    return;
  }
  var count = evs.length > 2 ? '<span class="cal-count">'+evs.length+'</span>' : '';
  var linhas = evs.slice(0, 2).map(function(e){
    return '<div class="ev-line" data-ev="'+e.id+'"><span class="dot d-'+e.tipo+'"></span><span class="h">'+e.hora+'</span><span class="n">'+e.titulo+'</span></div>';
  }).join('');
  var mais = evs.length > 2 ? '<div class="ev-more">+'+(evs.length - 2)+' mais</div>' : '';
  cel.innerHTML = '<div class="cal-head">'+numHtml+count+'</div><div class="evs">'+linhas+'</div>'+mais;
}
// Tira o evento cancelado do calendário (linha na célula + caixa do dia se estiver
// aberta) e do EVENTOS_DIA em memória, sem precisar recarregar a página.
function removerEventoDoCalendario(id){
  var isoAlvo = null;
  for(var iso in EVENTOS_DIA){
    if(EVENTOS_DIA[iso].eventos.some(function(e){ return String(e.id) === String(id); })){ isoAlvo = iso; break; }
  }
  if(isoAlvo){
    EVENTOS_DIA[isoAlvo].eventos = EVENTOS_DIA[isoAlvo].eventos.filter(function(e){ return String(e.id) !== String(id); });
    if(!EVENTOS_DIA[isoAlvo].eventos.length) delete EVENTOS_DIA[isoAlvo];
    renderizarCelula(isoAlvo);
  }
  var devRow = document.querySelector('#daybox .dev[data-ev="'+id+'"]');
  if(devRow){
    devRow.remove();
    var restBox = document.querySelectorAll('#daybox .dev').length;
    if(!restBox){ fecharDia(); return; }
    var sub = document.querySelector('#daybox .daybox-sub');
    if(sub) sub.textContent = restBox + (restBox===1?' compromisso':' compromissos');
  }
}
function agNovoNoDia(iso){
  fecharDia();
  agNovo(true);
  var i = document.querySelector('#novo input[name=data]');
  if(i) i.value = iso;
}
// Delegado no calendário (não em cada célula): células podem ser recriadas por
// renderizarCelula() depois de um cancelamento, então o listener direto se perderia.
document.querySelector('.cal').addEventListener('click', function(ev){
  var cel = ev.target.closest('.cal-cell.tem-evento');
  if(cel) abrirDia(cel.getAttribute('data-iso'));
});
document.querySelector('.cal').addEventListener('keydown', function(ev){
  if(ev.key!=='Enter' && ev.key!==' ') return;
  var cel = ev.target.closest('.cal-cell.tem-evento');
  if(cel){ ev.preventDefault(); abrirDia(cel.getAttribute('data-iso')); }
});
document.getElementById('dayOverlay').addEventListener('click', function(ev){ if(ev.target.id==='dayOverlay') fecharDia(); });
document.addEventListener('keydown', function(ev){ if(ev.key==='Escape') fecharDia(); });

function agNovo(v){var n=document.getElementById('novo');if(n){n.style.display='';n.scrollIntoView({behavior:'smooth',block:'center'});var i=n.querySelector('input[name=titulo]');if(i)setTimeout(function(){i.focus()},300);}return false;}
function agCopiar(){var i=document.getElementById('feedUrl');if(!i)return;i.select();try{document.execCommand('copy');}catch(e){}if(navigator.clipboard)navigator.clipboard.writeText(i.value);var b=event.target;var t=b.textContent;b.textContent='Copiado ✓';setTimeout(function(){b.textContent=t},1600);}
function cpRow(b){var row=b.closest('.share-row');if(!row)return;var txt=row.getAttribute('data-link')||'';if(navigator.clipboard)navigator.clipboard.writeText(txt);var t=b.textContent;b.textContent='✓';setTimeout(function(){b.textContent=t},1400);}
function addGuest(){var box=document.getElementById('guests');var d=document.createElement('div');d.className='guest-row';d.innerHTML='<div><input class="gnome" name="convidado_nome" placeholder="Nome" autocomplete="off"></div><div><input name="convidado_contato" placeholder="(86) 90000-0000" autocomplete="off"></div><button type="button" class="g-rm" onclick="rmGuest(this)" title="Remover" aria-label="Remover">✕</button>';box.appendChild(d);var i=d.querySelector('input');if(i)i.focus();}
function rmGuest(b){var box=document.getElementById('guests');var row=b.closest('.guest-row');if(box&&box.children.length>1){row.remove();}else{row.querySelectorAll('input').forEach(function(x){x.value='';});}}

// ---------------- Local: busca de endereço (Google Places) + manual + enviar ----------------
(function(){
  var addrInput = document.getElementById('addrInput');
  var addrDrop = document.getElementById('addrDrop');
  var addrPicked = document.getElementById('addrPicked');
  var localHidden = document.getElementById('localHidden');
  var hintLine = document.getElementById('hintLine');
  var addrWrap = document.getElementById('addrWrap');
  var searchRow = document.querySelector('.addr-input-row');
  var altRow = document.querySelector('.local-alt-row');
  var manualToggle = document.getElementById('manualToggle');
  var manualBox = document.getElementById('manualBox');
  var manualInput = document.getElementById('manualInput');
  var manualCancel = document.getElementById('manualCancel');
  var onlineToggle = document.getElementById('onlineToggle');
  var onlineBox = document.getElementById('onlineBox');
  var onlineCancel = document.getElementById('onlineCancel');
  if(!addrInput) return;

  function nomesEnvolvidos(){
    var nomes = [];
    document.querySelectorAll('.gnome').forEach(function(i){ if(i.value.trim()) nomes.push(i.value.trim()); });
    return nomes;
  }
  function fraseEnvolvidos(){
    var n = nomesEnvolvidos();
    if(n.length < 2) return '';
    return n.slice(0, -1).join(', ') + ' e ' + n[n.length - 1];
  }
  function linkMapa(endereco){
    return 'https://www.google.com/maps/search/?api=1&query=' + encodeURIComponent(endereco);
  }

  // Geolocalização: só pede no primeiro foco no campo (não no load da página,
  // pra não estourar o popup de permissão sem contexto). Se negar/não suportar,
  // segue sem — a busca continua igual a antes, só sem o bias de proximidade.
  var geoCoords = null, geoPedido = false;
  addrInput.addEventListener('focus', function(){
    if(geoPedido || !navigator.geolocation) return;
    geoPedido = true;
    navigator.geolocation.getCurrentPosition(function(pos){
      geoCoords = { lat: pos.coords.latitude, lng: pos.coords.longitude };
    }, function(){ /* negado ou indisponível: segue sem bias */ }, { timeout: 6000 });
  });

  var timer = null;
  addrInput.addEventListener('input', function(){
    localHidden.value = addrInput.value;    // digitou solto -> já vale como local, igual antes
    addrPicked.innerHTML = '';
    var termo = addrInput.value.trim();
    clearTimeout(timer);
    if(termo.length < 2){ addrDrop.classList.remove('show'); return; }
    timer = setTimeout(function(){ buscar(termo); }, 320);
  });

  function buscar(termo){
    var url = '/painel/agenda/buscar-local?q=' + encodeURIComponent(termo);
    if(geoCoords) url += '&lat=' + geoCoords.lat + '&lng=' + geoCoords.lng;
    fetch(url)
      .then(function(r){ return r.json(); })
      .then(function(d){
        if(addrInput.value.trim() !== termo) return;   // resposta atrasada de uma busca antiga
        if(!d.ok && d.erro === 'sem_chave'){
          addrDrop.classList.remove('show');
          hintLine.textContent = 'Busca de endereço desligada nesta conta — digite manualmente.';
          return;
        }
        var itens = (d.itens || []);
        if(!itens.length){
          addrDrop.innerHTML = '<div class="addr-empty">Nenhum lugar encontrado pra "'+termo+'". Usa o link "digitar manualmente" abaixo.</div>';
        } else {
          addrDrop.innerHTML = itens.map(function(p, idx){
            return '<div class="addr-opt" data-idx="'+idx+'"><span class="pin">📍</span>'
              + '<div><div class="tt"></div><div class="ad"></div></div></div>';
          }).join('');
          // texto via textContent (não via template) pra não abrir brecha de HTML no nome/endereço do lugar
          Array.prototype.forEach.call(addrDrop.querySelectorAll('.addr-opt'), function(row, idx){
            row.querySelector('.tt').textContent = itens[idx].nome;
            row.querySelector('.ad').textContent = itens[idx].endereco;
            row.addEventListener('click', function(){ escolherLocal(itens[idx]); });
          });
        }
        addrDrop.classList.add('show');
      })
      .catch(function(){ addrDrop.classList.remove('show'); });
  }

  function escolherLocal(p){
    addrInput.value = p.nome;
    localHidden.value = p.nome;
    addrDrop.classList.remove('show');
    hintLine.textContent = 'Endereço confirmado pelo Google — o link do mapa vai junto nas mensagens.';
    var mapa = linkMapa(p.endereco);
    var card = document.createElement('div');
    card.className = 'addr-picked';
    var chk = document.createElement('span'); chk.className = 'chk'; chk.textContent = '✅';
    var body = document.createElement('div'); body.className = 'body';
    var nm = document.createElement('div'); nm.className = 'nm'; nm.textContent = p.nome;
    var ed = document.createElement('div'); ed.className = 'ed'; ed.textContent = p.endereco;
    var lnk = document.createElement('a'); lnk.className = 'lnk'; lnk.href = mapa; lnk.target = '_blank';
    lnk.rel = 'noopener'; lnk.textContent = '🔗 Ver no Google Maps';
    body.appendChild(nm); body.appendChild(ed); body.appendChild(lnk);
    var x = document.createElement('button'); x.type = 'button'; x.className = 'x';
    x.setAttribute('aria-label', 'Trocar endereço'); x.textContent = '✕';
    x.addEventListener('click', function(){
      addrInput.value = ''; localHidden.value = ''; addrPicked.innerHTML = '';
      hintLine.textContent = 'Digite pra buscar — ex: nome do lugar ou endereço.';
      addrInput.focus();
    });
    var actions = document.createElement('div'); actions.className = 'addr-actions';
    var send = document.createElement('button'); send.type = 'button'; send.className = 'send-btn';
    send.textContent = '💬 Enviar pro cliente';
    var pop = document.createElement('div'); pop.className = 'pop';
    send.addEventListener('click', function(){
      var titulo = (document.getElementById('fTitulo').value || 'Compromisso').trim();
      var data = document.getElementById('fData').value, hora = document.getElementById('fHora').value;
      var quando = (data ? data.split('-').reverse().join('/') : '') + (hora ? ' ' + hora : '');
      var frase = fraseEnvolvidos();
      var msgTxt = titulo + '\\n' + quando + ' — ' + p.nome + '\\n📍 ' + mapa + (frase ? '\\n👥 Com: ' + frase : '');
      pop.innerHTML = '';
      var h4 = document.createElement('h4'); h4.textContent = '📍 Enviar pro cliente';
      var box = document.createElement('div'); box.className = 'pop-msg'; box.textContent = msgTxt;
      var go = document.createElement('a'); go.className = 'pop-go'; go.target = '_blank'; go.rel = 'noopener';
      go.href = 'https://wa.me/?text=' + encodeURIComponent(msgTxt); go.textContent = '💬 Abrir WhatsApp';
      pop.appendChild(h4); pop.appendChild(box); pop.appendChild(go);
      pop.classList.toggle('show');
    });
    actions.appendChild(send); actions.appendChild(pop);
    card.appendChild(chk); card.appendChild(body); card.appendChild(x); card.appendChild(actions);
    addrPicked.innerHTML = '';
    addrPicked.appendChild(card);
  }

  function voltarBusca(){
    searchRow.style.display = ''; hintLine.style.display = ''; altRow.style.display = '';
    addrInput.value = ''; localHidden.value = '';
  }

  manualToggle.addEventListener('click', function(){
    searchRow.style.display = 'none';
    addrDrop.classList.remove('show');
    addrPicked.innerHTML = '';
    hintLine.style.display = 'none';
    altRow.style.display = 'none';
    manualBox.classList.add('show');
    addrInput.value = ''; localHidden.value = '';
    manualInput.focus();
  });
  manualInput.addEventListener('input', function(){ localHidden.value = manualInput.value; });
  manualCancel.addEventListener('click', function(){
    manualBox.classList.remove('show');
    manualInput.value = '';
    voltarBusca();
    addrInput.focus();
  });

  // Reunião online: sem endereço nenhum — fecha a busca e nunca manda link de
  // mapa (nem no convite, nem quando o convidado confirma presença).
  onlineToggle.addEventListener('click', function(){
    searchRow.style.display = 'none';
    addrDrop.classList.remove('show');
    addrPicked.innerHTML = '';
    hintLine.style.display = 'none';
    altRow.style.display = 'none';
    onlineBox.classList.add('show');
    addrInput.value = ''; localHidden.value = 'Online';
  });
  onlineCancel.addEventListener('click', function(){
    onlineBox.classList.remove('show');
    voltarBusca();
    addrInput.focus();
  });

  document.addEventListener('click', function(ev){
    if(!addrWrap.contains(ev.target)) addrDrop.classList.remove('show');
    if(!ev.target.closest('.addr-actions')){
      document.querySelectorAll('.pop.show').forEach(function(p){ p.classList.remove('show'); });
    }
  });
})();
// Feedback instantâneo: ao enviar QUALQUER form, o botão reage na hora (apaga +
// "⏳ …"), enquanto o back processa — pro clique nunca parecer travado. Como a
// maioria recarrega, isso só precisa durar até a navegação; o timeout destrava
// caso algo impeça o envio (ex.: erro de rede). Respeita confirm() cancelado.
document.addEventListener('submit', function(ev){
  if(ev.defaultPrevented) return;                         // ex.: Cancelar -> confirm() = não
  var f=ev.target; if(!f||f.tagName!=='FORM') return;
  if(f.dataset && f.dataset.ajax) return;                 // forms AJAX se gerenciam sozinhos
  var b=f.querySelector('button[type=submit],button:not([type])');
  if(!b||b.disabled) return;
  var busy=b.getAttribute('data-busy');
  b.dataset.orig=b.innerHTML;
  if(busy) b.innerHTML=busy;
  b.classList.add('is-busy'); b.disabled=true;
  setTimeout(function(){ if(b.dataset.orig!=null){ b.innerHTML=b.dataset.orig; b.disabled=false; b.classList.remove('is-busy'); delete b.dataset.orig; } }, 8000);
}, false);

// Toast flutuante
function zaqToast(msg, ok){
  var t=document.createElement('div');
  t.className='zaq-toast'+(ok===false?' err':'');
  t.textContent=msg; document.body.appendChild(t);
  requestAnimationFrame(function(){ t.classList.add('show'); });
  setTimeout(function(){ t.classList.remove('show'); setTimeout(function(){ t.remove(); },300); }, 3400);
}
function zaqVazioProximos(){
  var px=document.querySelector('.px'); if(!px) return;
  if(!px.querySelector('.px-row') && !px.querySelector('.px-vazio')){
    var d=document.createElement('div'); d.className='px-vazio';
    d.textContent='Nada por vir. Marque um compromisso ali em cima. 🎉'; px.appendChild(d);
  }
}
// Ações SEM reload (fetch): Cancelar remove a linha/chip na hora; 📲 Zaq envia e
// dá um toast. Fallback: sem JS, os <form> continuam funcionando por POST normal.
document.addEventListener('submit', function(ev){
  var f=ev.target; if(!f||!f.dataset||!f.dataset.ajax) return;
  if(ev.defaultPrevented) return;                         // confirm() cancelado
  ev.preventDefault();
  var tipo=f.dataset.ajax, btn=f.querySelector('button[type=submit]');
  var orig=btn?btn.innerHTML:'';
  if(btn){ btn.disabled=true; btn.classList.add('is-busy'); if(btn.getAttribute('data-busy')) btn.innerHTML=btn.getAttribute('data-busy'); }
  function restore(){ if(btn){ btn.disabled=false; btn.classList.remove('is-busy'); btn.innerHTML=orig; } }
  fetch(f.action, {method:'POST', headers:{'X-Zaq-Ajax':'1'}, body:new FormData(f)})
    .then(function(r){ return r.json(); })
    .then(function(d){
      if(tipo==='cancelar'){
        if(d && d.ok){
          var id=(f.querySelector('[name=evento_id]')||{}).value;
          var row=f.closest('.px-row'); if(row){ row.classList.add('saindo'); setTimeout(function(){ row.remove(); zaqVazioProximos(); },200); }
          if(id) removerEventoDoCalendario(id);
          zaqToast('Compromisso cancelado.');
        } else { restore(); zaqToast('Não consegui cancelar.', false); }
      } else {   // enviar convite
        if(d && d.ok){ zaqToast(d.msg||'Convite enviado! ✅'); if(btn){ btn.innerHTML='✓ Enviado'; btn.classList.remove('is-busy'); } }
        else { restore(); zaqToast((d && d.msg)||'Não consegui enviar.', false); }
      }
    })
    .catch(function(){ restore(); zaqToast('Erro de conexão — tenta de novo.', false); });
}, false);
</script>
{% endblock %}"""

_env.loader.mapping["agenda"] = _AGENDA_TPL


# ---------------------------------------------------------------- página pública do convite
_CONVITE_TPL = """<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>Convite — {{ empresa }}</title>
<style>
  :root{--bg:#0b0f0d;--card:#141815;--card2:#1b201c;--borda:#2a302b;--txt:#eaeae6;
    --mut:#9a9a94;--verde:#1d9e75;--verde2:#25d366;--verde-cl:#5dcaa5;--azul:#3987e5;--verm:#e0574f}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--txt);font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
    line-height:1.5;-webkit-font-smoothing:antialiased;display:flex;justify-content:center;padding:24px 16px 48px}
  .wrap{width:100%;max-width:440px}
  .brand{display:flex;align-items:center;gap:8px;justify-content:center;margin-bottom:18px;color:var(--verde-cl);font-weight:800;letter-spacing:-.01em}
  .brand svg{width:22px;height:22px}
  .card{background:var(--card);border:1px solid var(--borda);border-radius:18px;padding:22px;box-shadow:0 20px 50px rgba(0,0,0,.35)}
  .emp{font-size:.78rem;color:var(--mut);text-transform:uppercase;letter-spacing:.08em;font-weight:700}
  h1{font-size:1.28rem;margin:.3rem 0 2px;letter-spacing:-.01em;text-wrap:balance}
  .greet{color:var(--mut);font-size:.95rem;margin:0 0 16px}
  .ev{background:var(--card2);border:1px solid var(--borda);border-left:4px solid var(--azul);border-radius:12px;padding:14px 16px;margin-bottom:20px}
  .ev .t{font-weight:700;font-size:1.02rem}
  .ev .row{display:flex;align-items:center;gap:8px;color:var(--mut);font-size:.9rem;margin-top:7px}
  .ev .row svg{width:16px;height:16px;flex:0 0 16px;stroke:var(--verde-cl)}
  .btn{display:block;width:100%;text-align:center;border:0;border-radius:12px;padding:.85rem;font-size:1rem;
    font-weight:700;cursor:pointer;font-family:inherit;margin-bottom:10px}
  .b-conf{background:var(--verde2);color:#04160e}
  .b-conf:hover{background:#2ee578}
  .b-sec{background:transparent;border:1px solid var(--borda);color:var(--mut);font-weight:600;font-size:.92rem}
  .b-sec:hover{border-color:var(--verde);color:var(--txt)}
  .expand{display:none;margin:-2px 0 10px;padding:12px;background:var(--card2);border:1px solid var(--borda);border-radius:12px}
  .expand.open{display:block}
  textarea{width:100%;background:var(--bg);border:1px solid var(--borda);border-radius:9px;color:var(--txt);
    padding:.6rem;font-size:.92rem;font-family:inherit;resize:vertical;min-height:64px;margin-bottom:10px}
  textarea:focus{outline:0;border-color:var(--verde)}
  .b-go{background:var(--card);border:1px solid var(--verde);color:var(--verde-cl);font-weight:700;font-size:.9rem;padding:.6rem;margin:0}
  .res{text-align:center;padding:8px 0 2px}
  .res .ic{font-size:2.6rem;line-height:1}
  .res h2{font-size:1.2rem;margin:.5rem 0 .3rem}
  .res p{color:var(--mut);font-size:.95rem;margin:0 0 4px}
  .res .quote{margin-top:10px;font-size:.88rem;color:var(--mut);font-style:italic}
  .cal{display:inline-flex;align-items:center;gap:8px;margin-top:16px;background:var(--card2);border:1px solid var(--borda);
    color:var(--txt);border-radius:10px;padding:.7rem 1.1rem;text-decoration:none;font-weight:700;font-size:.92rem}
  .cal:hover{border-color:var(--verde)}
  .foot{text-align:center;color:var(--mut);font-size:.74rem;margin-top:18px}
  .foot b{color:var(--verde-cl)}
</style></head><body>
<div class="wrap">
  <div class="brand">
    <svg viewBox="0 0 64 64" fill="none"><path d="M16 18 H44 L18 46 H46" stroke="#3ee0a6" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/><path d="M47 10 L49 16 L55 18 L49 20 L47 26 L45 20 L39 18 L45 16 Z" fill="#3ee0a6"/></svg>
    zaq
  </div>
  <div class="card">
    {% if not estado %}
    <div class="emp">{{ empresa }}</div>
    <h1>Convite de reunião</h1>
    <p class="greet">{% if nome %}Oi, {{ nome }}! {% endif %}A {{ empresa }} quer marcar com você:</p>
    <div class="ev">
      <div class="t">{{ titulo }}</div>
      <div class="row"><svg viewBox="0 0 24 24" fill="none" stroke-width="2"><rect x="3.5" y="5" width="17" height="15" rx="2"/><path d="M3.5 9.5h17M8 3v4M16 3v4"/></svg>{{ quando }}</div>
      {% if local %}<div class="row"><svg viewBox="0 0 24 24" fill="none" stroke-width="2"><path d="M12 21s7-5.5 7-11a7 7 0 10-14 0c0 5.5 7 11 7 11z"/><circle cx="12" cy="10" r="2.5"/></svg>{{ local }}</div>{% endif %}
    </div>
    <form method="post" action="/convite/{{ token }}/responder">
      <input type="hidden" name="acao" value="confirmar">
      <button class="btn b-conf" type="submit">✅ Confirmar presença</button>
    </form>
    <button class="btn b-sec" type="button" onclick="tog('rem')">🔁 Remarcar — sugerir outro horário</button>
    <div class="expand" id="rem">
      <form method="post" action="/convite/{{ token }}/responder">
        <input type="hidden" name="acao" value="remarcar">
        <textarea name="resposta" placeholder="Se quiser, sugira um horário melhor (opcional)"></textarea>
        <button class="btn b-go" type="submit">Enviar pedido de remarcar</button>
      </form>
    </div>
    <button class="btn b-sec" type="button" onclick="tog('rec')">❌ Não vou poder</button>
    <div class="expand" id="rec">
      <form method="post" action="/convite/{{ token }}/responder">
        <input type="hidden" name="acao" value="recusar">
        <textarea name="resposta" placeholder="Motivo (opcional)"></textarea>
        <button class="btn b-go" type="submit">Confirmar que não vou</button>
      </form>
    </div>
    {% else %}
    <div class="res">
      {% if estado == 'confirmado' %}
        <div class="ic">✅</div><h2>Presença confirmada!</h2>
        <p>Você confirmou <b style="color:var(--txt)">{{ titulo }}</b> — {{ quando }}.</p>
        <p>A {{ empresa }} já foi avisada. Até lá! 👋</p>
        <div style="display:flex;flex-direction:column;gap:8px;margin-top:16px">
          {% if cal_link %}<a class="cal" href="{{ cal_link }}" style="margin-top:0" target="_blank" rel="noopener">📆 Adicionar ao meu calendário</a>{% endif %}
          {% if mapa_link %}<a class="cal" href="{{ mapa_link }}" style="margin-top:0" target="_blank" rel="noopener">📍 Ver local no mapa{% if local %} — {{ local }}{% endif %}</a>{% endif %}
        </div>
      {% elif estado == 'remarcar' %}
        <div class="ic">🔁</div><h2>Pedido enviado</h2>
        <p>Avisamos a {{ empresa }} que você prefere outro horário pra <b style="color:var(--txt)">{{ titulo }}</b>.</p>
        {% if resposta %}<div class="quote">“{{ resposta }}”</div>{% endif %}
        <p style="margin-top:8px">Ela vai te procurar pra ajustar. 🙂</p>
      {% else %}
        <div class="ic">👍</div><h2>Tudo certo, obrigado por avisar</h2>
        <p>Avisamos a {{ empresa }} que você não vai poder na <b style="color:var(--txt)">{{ titulo }}</b>.</p>
        {% if resposta %}<div class="quote">“{{ resposta }}”</div>{% endif %}
        <p style="margin-top:8px">Se mudar de ideia, é só falar com ela. 👋</p>
      {% endif %}
    </div>
    {% endif %}
  </div>
  <div class="foot">Agendado com <b>Zaq</b> · seu assistente financeiro</div>
</div>
<script>
function tog(id){var e=document.getElementById(id);var open=e.classList.contains('open');
  document.querySelectorAll('.expand').forEach(function(x){x.classList.remove('open')});
  if(!open){e.classList.add('open');var t=e.querySelector('textarea');if(t)setTimeout(function(){t.focus()},120);}}
</script>
</body></html>"""

_CONVITE_404_TPL = """<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Convite não encontrado</title>
<style>body{margin:0;background:#0b0f0d;color:#eaeae6;font-family:system-ui,sans-serif;display:flex;
  min-height:100vh;align-items:center;justify-content:center;text-align:center;padding:24px}
  .b{max-width:360px}.b h1{font-size:1.3rem;margin:.4rem 0}.b p{color:#9a9a94}</style></head><body>
<div class="b"><div style="font-size:2.4rem">🔍</div><h1>Convite não encontrado</h1>
<p>Esse link de convite não existe mais ou foi digitado errado. Peça um novo pra quem te convidou.</p></div>
</body></html>"""

_env.loader.mapping["convite"] = _CONVITE_TPL
_env.loader.mapping["convite_404"] = _CONVITE_404_TPL
