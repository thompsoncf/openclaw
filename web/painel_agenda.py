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
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from db.conexao import get_pool
from finance import agenda as ag
from finance import convites as cv
from finance import notificar
from web.portal import _env, _render, conta_logada

router = APIRouter()

MESES = ["", "janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
         "agosto", "setembro", "outubro", "novembro", "dezembro"]
DIAS_SEM = ["Dom", "Seg", "Ter", "Qua", "Qui", "Sex", "Sáb"]
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
    lista = []
    for g in guests:
        url = _convite_url(request, g["token"])
        msg = (f"Oi{(' ' + g['nome']) if g['nome'] else ''}! Quero marcar uma reunião: "
               f"{ev['titulo']} — {quando}{(' (' + local + ')') if local else ''}. "
               f"Confirma pra mim aqui: {url}")
        lista.append({"nome": g["nome"], "contato": g["contato"], "url": url,
                      "wa": _wa_share(g["contato"] or "", msg),
                      "status": g["status"], "status_rot": g["status_rot"]})
    return {"titulo": ev["titulo"], "quando": quando, "guests": lista,
            "total": len(lista), "resumo": cv.resumo(guests)}


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
    convidados = cv.por_evento(pool, conta_id, [e["id"] for e in proximos])
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
                   status_rot=cv.STATUS_ROT,
                   mes_prev=_vizinho(ano, mes, -1), mes_next=_vizinho(ano, mes, +1),
                   mes_hoje=f"{hoje.year:04d}-{hoje.month:02d}",
                   hoje_iso=hoje.isoformat(), abrir_novo=(novo == "1"),
                   cfg=cfg, feed_url=feed_url, share=share,
                   aviso=request.session.pop("agenda_aviso", None))


# ================================================================ NOVO
@router.post("/painel/agenda/novo")
def agenda_novo(request: Request, titulo: str = Form(...), data: str = Form(""),
                hora: str = Form(""), local: str = Form(""), tipo: str = Form("pessoal"),
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


# ================================================================ CANCELAR
@router.post("/painel/agenda/cancelar")
def agenda_cancelar(request: Request, evento_id: int = Form(...), m: str = Form("")):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    if ag.cancelar_evento(get_pool(), ctx["conta_id"], evento_id):
        request.session["agenda_aviso"] = "Compromisso cancelado."
    voltar = f"/painel/agenda?m={m}" if m else "/painel/agenda"
    return RedirectResponse(voltar, status_code=303)


# ================================================================ LEMBRETE (opt-in)
@router.post("/painel/agenda/lembrete")
def agenda_lembrete(request: Request, resumo_dia: str = Form(""),
                    hora_resumo: str = Form("7"), aviso: str = Form(""),
                    aviso_antes_min: str = Form("30"), m: str = Form("")):
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
                     aviso_antes_min=antes)
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
    notificar.avisar_dono_convite(pool, c["conta_id"], c["nome"] or "O convidado",
                                  c["evento"]["titulo"], ag.fmt_hora(c["evento"]),
                                  status, c.get("resposta") or "")
    # Grupo (2+): quando o último responde, avisa que fechou.
    grupo = cv.por_evento(pool, c["conta_id"], [c["evento"]["id"]]).get(c["evento"]["id"], [])
    if len(grupo) > 1:
        r = cv.resumo(grupo)
        if r["fechado"]:
            notificar.avisar_dono_grupo_fechado(
                pool, c["conta_id"], c["evento"]["titulo"], ag.fmt_hora(c["evento"]),
                r["confirmados"], r["remarcar"], r["recusados"])
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
.ag-btn{display:inline-flex;align-items:center;gap:7px;background:var(--verde);color:#04160e;font-weight:700;border:0;border-radius:10px;padding:.6rem 1rem;font-size:.92rem;cursor:pointer;text-decoration:none}
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
.cal-cell{min-height:92px;border-right:1px solid var(--borda);border-bottom:1px solid var(--borda);padding:6px 6px 5px;display:flex;flex-direction:column;gap:3px}
.cal-cell:nth-child(7n){border-right:0}
.cal-wk:last-child .cal-cell{border-bottom:0}
.cal-cell.fora{background:rgba(255,255,255,.012)}
.cal-cell.fora .cal-num{color:#4a4a4c}
.cal-num{font-size:.8rem;color:var(--txt-mut);font-variant-numeric:tabular-nums;align-self:flex-end;line-height:1}
.cal-cell.hoje .cal-num{background:var(--verde);color:#04160e;font-weight:800;width:22px;height:22px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center}
.chip{font-size:.68rem;line-height:1.25;padding:2px 6px;border-radius:5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;border-left:3px solid;background:var(--card-2)}
.chip b{font-variant-numeric:tabular-nums;font-weight:700;opacity:.85;margin-right:3px}
.t-pessoal{border-color:var(--verde-claro);color:#bfeeda}
.t-empresa{border-color:#3987e5;color:#bcd8f6}
.t-fornecedor{border-color:#e0a33e;color:#f0d9a6}
.chip.t-pessoal{background:rgba(29,158,117,.12)}
.chip.t-empresa{background:rgba(57,135,229,.12)}
.chip.t-fornecedor{background:rgba(224,163,62,.12)}
.cal-mais{font-size:.65rem;color:var(--txt-mut);padding-left:3px}
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
.px-x{background:none;border:0;color:var(--txt-mut);cursor:pointer;font-size:1rem;line-height:1;padding:2px 4px;border-radius:6px}
.px-x:hover{color:#f0917f;background:rgba(224,87,79,.12)}
.px-vazio{color:var(--txt-mut);font-size:.86rem;padding:8px 2px}
/* formulários / cards laterais */
.side-cards{display:flex;flex-direction:column;gap:18px}
.frm label{display:block;font-size:.74rem;color:var(--txt-mut);margin:10px 0 4px;font-weight:600}
.frm input,.frm select{width:100%;background:var(--card-2);border:1px solid var(--borda);border-radius:9px;color:var(--txt);padding:.55rem .6rem;font-size:.92rem;font-family:inherit}
.frm input:focus,.frm select:focus{outline:0;border-color:var(--verde)}
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
.feed{display:flex;gap:8px;margin:2px 0 10px}
.feed input{flex:1;background:var(--card-2);border:1px solid var(--borda);border-radius:9px;color:var(--txt);padding:.5rem .6rem;font-size:.8rem;font-family:ui-monospace,monospace}
.feed button{background:var(--card-2);border:1px solid var(--borda);border-radius:9px;color:var(--txt);padding:0 12px;cursor:pointer;font-size:.84rem}
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
.sr-cp{background:var(--card);border:1px solid var(--borda);color:var(--txt);border-radius:8px;padding:.42rem .55rem;cursor:pointer;font-size:.86rem}
.sr-cp:hover{border-color:var(--verde)}
/* convidado / bloco no form */
.gconv{margin-top:14px;padding-top:14px;border-top:1px dashed var(--borda)}
.gconv .gt{font-size:.82rem;color:var(--verde-claro);font-weight:700}
.gconv .gd{font-size:.74rem;color:var(--txt-mut);margin:1px 0 8px}
.guest-row{display:grid;grid-template-columns:1fr 1fr auto;gap:8px;margin-bottom:8px}
.guest-row .g-rm{background:transparent;border:1px solid var(--borda);color:var(--txt-mut);border-radius:8px;width:38px;cursor:pointer;font-size:.9rem}
.guest-row .g-rm:hover{border-color:#e0574f;color:#f0917f}
.g-add{background:transparent;border:1px dashed var(--borda);color:var(--verde-claro);border-radius:9px;padding:.5rem;width:100%;cursor:pointer;font-weight:600;font-size:.84rem}
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
</style>"""

_AGENDA_TPL = """{% extends "base" %}{% block conteudo %}""" + _CSS + """
<div class="ag-wrap">
  {% if aviso %}<div class="ag-aviso">{{ aviso }}</div>{% endif %}
  {% if share %}
  <div class="share">
    <h2>✅ {{ share.total }} convite{{ 's' if share.total != 1 }} pronto{{ 's' if share.total != 1 }} pra enviar</h2>
    <p><b>{{ share.titulo }}</b> — {{ share.quando }}. Mande o link de cada um; cada pessoa confirma o seu e você é avisado aqui.</p>
    <div class="share-list">
      {% for g in share.guests %}
      <div class="share-row" data-link="{{ g.url }}">
        <div class="sr-av">{{ (g.nome or '?')[0]|upper }}</div>
        <div class="sr-who"><b>{{ g.nome or 'Convidado' }}</b><small>{{ g.contato or 'sem número' }} · {{ g.status_rot }}</small></div>
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
          <div class="cal-cell{% if c.fora %} fora{% endif %}{% if c.hoje %} hoje{% endif %}">
            <span class="cal-num">{{ c.dia }}</span>
            {% for e in c.eventos[:3] %}
            <span class="chip t-{{ e.tipo }}" title="{{ e.hora }} {{ e.titulo }}"><b>{{ e.hora }}</b>{{ e.titulo }}</span>
            {% endfor %}
            {% if c.eventos|length > 3 %}<span class="cal-mais">+{{ c.eventos|length - 3 }} mais</span>{% endif %}
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
          <div class="px-row">
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
            <form method="post" action="/painel/agenda/cancelar" onsubmit="return confirm('Cancelar “{{ e.titulo }}”?')">
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
          <input name="titulo" placeholder="Ex: reunião com o contador" required autocomplete="off">
          <div class="row2">
            <div><label>Data</label><input name="data" type="date" value="{{ hoje_iso }}" required></div>
            <div><label>Hora</label><input name="hora" type="time" value="09:00"></div>
          </div>
          <label>Local <span style="font-weight:400">(opcional)</span></label>
          <input name="local" placeholder="Ex: escritório, online…" autocomplete="off">
          <label>Tipo</label>
          <div class="segs">
            <label class="s-pessoal"><input type="radio" name="tipo" value="pessoal" checked><span>Pessoal</span></label>
            <label class="s-empresa"><input type="radio" name="tipo" value="empresa"><span>Empresa</span></label>
            <label class="s-fornecedor"><input type="radio" name="tipo" value="fornecedor"><span>Fornecedor</span></label>
          </div>
          <div class="gconv">
            <div class="gt">👥 Convidar (opcional)</div>
            <div class="gd">Um ou vários. Cada convidado recebe o próprio link e confirma o seu — você vê "quantos de quantos".</div>
            <div id="guests">
              <div class="guest-row">
                <div><input name="convidado_nome" placeholder="Nome" autocomplete="off"></div>
                <div><input name="convidado_contato" placeholder="(86) 90000-0000" autocomplete="off"></div>
                <button type="button" class="g-rm" onclick="rmGuest(this)" title="Remover" aria-label="Remover">✕</button>
              </div>
            </div>
            <button type="button" class="g-add" onclick="addGuest()">+ adicionar convidado</button>
          </div>
          <button class="ok" type="submit">Marcar</button>
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
            <label class="sw"><input type="checkbox" name="aviso" value="1" {% if cfg.aviso_antes_min %}checked{% endif %} onchange="document.getElementById('subAviso').style.display=this.checked?'flex':'none'"><span class="track"></span><span class="knob"></span></label>
          </div>
          <div class="sub-opt" id="subAviso"{% if not cfg.aviso_antes_min %} style="display:none"{% endif %}>
            <select name="aviso_antes_min">
              {% for mn in [10,15,30,60,120] %}<option value="{{ mn }}" {% if cfg.aviso_antes_min==mn %}selected{% endif %}>{{ mn }} min antes</option>{% endfor %}
            </select>
          </div>
          <div class="canal-tag">📲 Vai chegar no seu WhatsApp/Telegram, onde você fala com o Zaq.</div>
          <button class="ok" type="submit">Salvar lembrete</button>
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
          <button class="ag-btn" type="submit" style="margin-top:6px">Ativar sincronização</button>
        </form>
        {% endif %}
      </div>
    </div>
  </div>
</div>
<script>
function agNovo(v){var n=document.getElementById('novo');if(n){n.style.display='';n.scrollIntoView({behavior:'smooth',block:'center'});var i=n.querySelector('input[name=titulo]');if(i)setTimeout(function(){i.focus()},300);}return false;}
function agCopiar(){var i=document.getElementById('feedUrl');if(!i)return;i.select();try{document.execCommand('copy');}catch(e){}if(navigator.clipboard)navigator.clipboard.writeText(i.value);var b=event.target;var t=b.textContent;b.textContent='Copiado ✓';setTimeout(function(){b.textContent=t},1600);}
function cpRow(b){var row=b.closest('.share-row');if(!row)return;var txt=row.getAttribute('data-link')||'';if(navigator.clipboard)navigator.clipboard.writeText(txt);var t=b.textContent;b.textContent='✓';setTimeout(function(){b.textContent=t},1400);}
function addGuest(){var box=document.getElementById('guests');var d=document.createElement('div');d.className='guest-row';d.innerHTML='<div><input name="convidado_nome" placeholder="Nome" autocomplete="off"></div><div><input name="convidado_contato" placeholder="(86) 90000-0000" autocomplete="off"></div><button type="button" class="g-rm" onclick="rmGuest(this)" title="Remover" aria-label="Remover">✕</button>';box.appendChild(d);var i=d.querySelector('input');if(i)i.focus();}
function rmGuest(b){var box=document.getElementById('guests');var row=b.closest('.guest-row');if(box&&box.children.length>1){row.remove();}else{row.querySelectorAll('input').forEach(function(x){x.value='';});}}
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
        {% if cal_link %}<a class="cal" href="{{ cal_link }}" target="_blank" rel="noopener">📆 Adicionar ao meu calendário</a>{% endif %}
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
