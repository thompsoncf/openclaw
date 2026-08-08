"""Cockpit do Vendedor — app mobile (PWA) do vendedor, servido fora do painel.

Espaço enxuto e mobile SÓ pro vendedor: recebe o lead, conversa (pelo chip da
empresa), mexe no funil, vê a ficha e fecha (ganho/perdido). Entra por LINK MÁGICO
(sem senha) — o gestor gera o link (botão 📱 Cockpit na Equipe) ou o vendedor pede
pelo próprio e-mail em /cockpit/login. A sessão (cookie assinado do portal) mantém
ele logado depois.

Páginas 100% server-rendered (HTML próprio, sem o layout do painel) + PWA
(manifest + service worker) pra instalar como app e, na PRÓXIMA fase, receber push.
Toda ação vai por form POST e redireciona — robusto, funciona sem JS. O motor (posse,
envio, funil) fica em finance/cockpit.py; aqui só HTTP + HTML.
"""
from __future__ import annotations

import html as _html

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from db.conexao import get_pool
from finance import cockpit as ck

router = APIRouter()

_PAPEIS_OK = ("vendedor", "gestor", "dono")


def esc(s) -> str:
    return _html.escape(str(s if s is not None else ""))


# ------------------------------------------------------------------ sessão
def _sessao(request: Request):
    """(conta_id, membro_id) do vendedor logado, ou None. Reusa a sessão do portal."""
    mid = request.session.get("membro_id")
    cid = request.session.get("conta_id")
    papel = request.session.get("papel", "dono")
    if not mid or not cid or papel not in _PAPEIS_OK:
        return None
    return cid, mid


# ------------------------------------------------------------------ shell/estilo
_CSS = """<style>
:root{--bg:#0e0e0f;--card:#161617;--card2:#1a1a1c;--borda:#2a2a2b;--txt:#ececec;--mut:#a8a8a3;
--verde:#1d9e75;--verde-claro:#5dcaa5;--azul:#5b9bd5;--amar:#e0a33e;--coral:#e0574f;--roxo:#c9a3e0;--zap:#25d366;
--fonte:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
*{box-sizing:border-box}html,body{margin:0}
body{background:var(--bg);color:var(--txt);font-family:var(--fonte);line-height:1.5;-webkit-font-smoothing:antialiased;
padding-bottom:env(safe-area-inset-bottom)}
a{color:inherit;text-decoration:none}
.wrap{max-width:520px;margin:0 auto;min-height:100vh;display:flex;flex-direction:column}
.hdr{display:flex;align-items:center;gap:.6rem;padding:.8rem 1rem;border-bottom:1px solid var(--borda);position:sticky;top:0;background:var(--bg);z-index:5}
.hdr .bk{color:var(--txt);font-size:1.4rem;padding:.1rem .3rem;margin-left:-.3rem}
.hdr .tt{flex:1;min-width:0}.hdr .tt b{font-size:1rem;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.hdr .tt small{color:var(--mut);font-size:.75rem}
.ib{width:36px;height:36px;border-radius:50%;background:#1d3a30;color:var(--verde-claro);display:grid;place-items:center;font-weight:700;flex-shrink:0}
.scroll{flex:1;overflow-y:auto}
.instpill{margin:.7rem 1rem;padding:.55rem .7rem;border:1px dashed #2f4a3f;border-radius:11px;background:rgba(29,158,117,.05);
display:flex;align-items:center;gap:.5rem;font-size:.8rem;color:var(--verde-claro)}
.instpill b{color:var(--txt)}
.lead{display:flex;gap:.65rem;align-items:center;padding:.75rem 1rem;border-bottom:1px solid var(--borda)}
.lead:active{background:var(--card2)}
.lead .dot{width:9px;height:9px;border-radius:50%;flex-shrink:0}
.lead .av{width:42px;height:42px;border-radius:50%;flex-shrink:0;display:grid;place-items:center;font-weight:700;background:#22252a;color:#cfe6dd}
.lead .mid{flex:1;min-width:0}
.lead .top{display:flex;justify-content:space-between;gap:.4rem}
.lead .emp{font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.lead .snip{color:var(--mut);font-size:.82rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin-top:.1rem}
.cchip{font-size:.68rem;padding:.08rem .45rem;border-radius:999px;border:1px solid;font-weight:600;flex-shrink:0;white-space:nowrap}
.cchip.ia{color:var(--roxo);border-color:#3a2b52;background:#1a1226}
.cchip.voce{color:var(--amar);border-color:#5a4520;background:#241c0f}
.vazio{padding:3rem 1.5rem;text-align:center;color:var(--mut)}.vazio .big{font-size:2.6rem}.vazio b{color:var(--txt);display:block;margin-top:.6rem}
.ficha{display:flex;gap:.4rem;padding:.6rem 1rem;flex-wrap:wrap;border-bottom:1px solid var(--borda)}
.fbtn{flex:1;min-width:76px;text-align:center;background:var(--card2);border:1px solid var(--borda);border-radius:9px;padding:.5rem .3rem;font-size:.78rem;color:var(--txt)}
.fbtn:active{background:#20232a}
.funil{padding:.6rem 1rem;border-bottom:1px solid var(--borda)}
.lbl{font-size:.68rem;color:var(--mut);text-transform:uppercase;letter-spacing:.04em;margin-bottom:.45rem}
.stchips{display:flex;gap:.35rem;flex-wrap:wrap}
.stf{display:inline}
.st{font-size:.78rem;padding:.32rem .62rem;border-radius:999px;border:1px solid var(--borda);background:var(--card2);color:var(--mut)}
.st.on{border-color:var(--verde);background:#10241a;color:var(--verde-claro);font-weight:600}
.wl{display:flex;gap:.4rem;margin-top:.55rem}
.wl .stf{flex:1}.wl button{width:100%}
.wl button,.perda button,.composer button,.gobtn{font:inherit;cursor:pointer}
.wl .win{border:1px solid #1e4a3a;color:var(--verde-claro);background:var(--card2);border-radius:9px;padding:.5rem;font-weight:600}
.wl .lose{border:1px solid #5a2b2b;color:#f0917f;background:var(--card2);border-radius:9px;padding:.5rem;font-weight:600;width:100%}
.perda{border-top:1px solid var(--borda)}
.perda summary{list-style:none;padding:.55rem 1rem;color:#f0917f;font-size:.82rem;font-weight:600}
.perda summary::-webkit-details-marker{display:none}
.perda form{padding:0 1rem 1rem;display:flex;flex-direction:column;gap:.5rem}
.perda select,.composer input,.login input{background:var(--card2);border:1px solid var(--borda);border-radius:10px;color:var(--txt);
padding:.6rem .8rem;font-size:.9rem;font-family:inherit;width:100%}
.perda .conf{background:#5a2b2b;color:#ffdad4;border:0;border-radius:10px;padding:.6rem;font-weight:700}
.chat{padding:.8rem 1rem;display:flex;flex-direction:column;gap:.5rem;background:#0c0c0d}
.iabar{font-size:.76rem;color:var(--roxo);background:#1a1226;border:1px solid #3a2b52;border-radius:9px;padding:.5rem .7rem;text-align:center}
.bub{max-width:80%;padding:.5rem .72rem;border-radius:13px;font-size:.9rem;line-height:1.4;word-wrap:break-word}
.bub.in{align-self:flex-start;background:var(--card2);border:1px solid var(--borda);border-bottom-left-radius:4px}
.bub.out{align-self:flex-end;background:#10362a;border:1px solid #1e4a3a;border-bottom-right-radius:4px}
.bub.ia{align-self:flex-end;background:#1a1226;border:1px solid #3a2b52;border-bottom-right-radius:4px}
.bub .who{font-size:.66rem;color:var(--mut);margin-bottom:.15rem}.bub.ia .who{color:var(--roxo)}
.assumir{margin:.7rem 1rem;padding:.6rem;border-radius:10px;border:1px solid var(--verde);background:#10241a;color:var(--verde-claro);font-weight:600;width:calc(100% - 2rem)}
.composer{display:flex;gap:.5rem;padding:.6rem;border-top:1px solid var(--borda);position:sticky;bottom:0;background:var(--bg)}
.composer input{border-radius:20px}
.composer button{width:44px;height:44px;border-radius:50%;background:var(--verde);color:#04140d;border:0;font-size:1.15rem;flex-shrink:0}
.msg{margin:.7rem 1rem;padding:.6rem .8rem;border-radius:10px;font-size:.85rem}
.msg.ok{border:1px solid #1e4a3a;background:#10241a;color:var(--verde-claro)}
.msg.err{border:1px solid #5a2b2b;background:#241313;color:#f0917f}
.login{flex:1;display:flex;flex-direction:column;justify-content:center;padding:2.4rem 1.6rem;gap:.5rem;text-align:center}
.login .logo{font-size:2.8rem}.login h2{margin:.2rem 0 0;font-size:1.35rem}.login p{color:var(--mut);margin:.2rem 0 1rem}
.login .go{background:var(--verde);color:#04140d;border:0;border-radius:11px;padding:.8rem;font-weight:700;font-size:.95rem;margin-top:.7rem}
.login small{color:var(--mut);font-size:.76rem;margin-top:.7rem}
.pcard{padding:1.1rem 1rem;border-bottom:1px solid var(--borda);display:flex;align-items:center;gap:.85rem}
.pcard .av{width:54px;height:54px;border-radius:50%;background:#1d3a30;color:var(--verde-claro);display:grid;place-items:center;font-weight:700;font-size:1.35rem}
.pcard b{font-size:1.08rem}.pcard small{color:var(--mut);display:block}
.kpis{display:flex;gap:.6rem;padding:1rem}.kpi{flex:1;background:var(--card2);border:1px solid var(--borda);border-radius:12px;padding:.75rem;text-align:center}
.kpi b{font-size:1.5rem;display:block}.kpi small{color:var(--mut);font-size:.72rem}
.prow{display:flex;align-items:center;justify-content:space-between;padding:.9rem 1rem;border-bottom:1px solid var(--borda);gap:1rem}
.prow .sub{color:var(--mut);font-size:.77rem}
.tgl{border:1px solid var(--borda);background:var(--card2);color:var(--mut);border-radius:999px;padding:.35rem .7rem;font-size:.8rem;font-weight:600;white-space:nowrap}
.tgl.on{border-color:var(--verde);background:#10241a;color:var(--verde-claro)}
.sair{margin:1.2rem 1rem;width:calc(100% - 2rem);padding:.65rem;border-radius:10px;border:1px solid #5a2b2b;background:#1a1010;color:#f0917f;font-weight:600}
.fsec{padding:.85rem 1rem;border-bottom:1px solid var(--borda)}
.frow{display:flex;justify-content:space-between;gap:.8rem;padding:.28rem 0;font-size:.9rem}
.frow span{color:var(--mut)}.frow b{text-align:right}
.tel{display:flex;align-items:center;gap:.5rem;padding:.45rem 0;font-size:.92rem;border-top:1px solid #202021}.tel:first-of-type{border-top:0}
.tel .z{margin-left:auto;font-size:.72rem;color:var(--zap);border:1px solid #16391f;background:#0c1c10;border-radius:999px;padding:.06rem .5rem}
</style>"""

_SW_REG = ("<script>if('serviceWorker' in navigator){"
           "navigator.serviceWorker.register('/cockpit/sw.js').catch(function(){});}</script>")


def _page(title: str, body: str) -> HTMLResponse:
    doc = ("<!doctype html><html lang=pt-br><head><meta charset=utf-8>"
           "<meta name=viewport content='width=device-width,initial-scale=1,viewport-fit=cover'>"
           "<meta name=theme-color content='#0e0e0f'>"
           "<link rel=manifest href='/cockpit/manifest.webmanifest'>"
           "<link rel=apple-touch-icon href='/cockpit/icon.svg'>"
           "<meta name=apple-mobile-web-app-capable content=yes>"
           "<meta name=mobile-web-app-capable content=yes>"
           "<meta name=apple-mobile-web-app-title content=Cockpit>"
           f"<title>{esc(title)}</title>{_CSS}</head><body><div class=wrap>"
           f"{body}</div>{_SW_REG}</body></html>")
    return HTMLResponse(doc)


def _ini(s: str) -> str:
    s = (s or "?").strip()
    return (s[0].upper() if s else "?")


# ------------------------------------------------------------------ login mágico
@router.get("/cockpit/login", response_class=HTMLResponse)
def cockpit_login_form(request: Request, enviado: str = ""):
    if _sessao(request):
        return RedirectResponse("/cockpit", status_code=303)
    if enviado:
        body = ("<div class=login><div class=logo>📬</div><h2>Confira seu e-mail</h2>"
                "<p>Se esse e-mail estiver cadastrado, você recebeu um link pra entrar "
                "no Cockpit. Ele vale por 15 minutos.</p>"
                "<small>Não chegou? Verifique o spam ou peça o link ao seu gestor.</small></div>")
        return _page("Cockpit — confira o e-mail", body)
    body = ("<div class=login><div class=logo>⚡</div><h2>Cockpit do Vendedor</h2>"
            "<p>Seu espaço pra atender leads. Sem senha — a gente manda um link pro seu e-mail.</p>"
            "<form method=post action='/cockpit/login'>"
            "<input name=email type=email required placeholder='seu e-mail' autocomplete=email>"
            "<button class=go type=submit>Enviar meu link de acesso</button></form>"
            "<small>🔒 O link vale por 15 min e abre no seu aparelho.</small></div>")
    return _page("Cockpit — entrar", body)


@router.post("/cockpit/login")
def cockpit_login(request: Request, email: str = Form(...)):
    pool = get_pool()
    achado = ck.membro_por_email(pool, email)
    if achado:                       # nunca revela se existe: sempre redireciona igual
        try:
            token = ck.gerar_token(pool, achado["conta_id"], achado["membro_id"])
            _enviar_link_email(pool, achado["conta_id"], (email or "").strip(), ck.link_acesso(token))
        except Exception:  # noqa: BLE001
            pass
    return RedirectResponse("/cockpit/login?enviado=1", status_code=303)


@router.get("/cockpit/entrar/{token}", response_class=HTMLResponse)
def cockpit_entrar(request: Request, token: str):
    dados = ck.validar_token(get_pool(), token)
    if not dados:
        body = ("<div class=login><div class=logo>⚠️</div><h2>Link expirado</h2>"
                "<p>Esse link já foi usado ou passou dos 15 minutos.</p>"
                "<a class=go href='/cockpit/login' style='display:block;text-decoration:none'>Pedir um novo link</a></div>")
        return _page("Cockpit — link expirado", body)
    request.session["conta_id"] = dados["conta_id"]
    request.session["membro_id"] = dados["membro_id"]
    request.session["papel"] = dados["papel"]
    request.session["cockpit"] = True
    return RedirectResponse("/cockpit", status_code=303)


@router.get("/cockpit/sair")
def cockpit_sair(request: Request):
    for k in ("cockpit", "membro_id", "papel"):
        request.session.pop(k, None)
    return RedirectResponse("/cockpit/login", status_code=303)


def _enviar_link_email(pool, conta_id: int, email: str, link: str) -> bool:
    """Manda o link mágico. Pela caixa da empresa (Canais) se houver; senão SMTP do Zaq."""
    titulo = "Seu acesso ao Cockpit"
    corpo = ("Toque no botão pra entrar no Cockpit e atender seus leads. "
             "O link vale por 15 minutos e abre no seu aparelho.")
    try:
        from finance import email_sender as es
        botao = (f'<div style="text-align:center;margin:24px 0">'
                 f'<a href="{esc(link)}" style="background:#1d9e75;color:#fff;padding:14px 28px;'
                 f'border-radius:10px;font-weight:600;text-decoration:none;display:inline-block">'
                 f'Entrar no Cockpit →</a></div>'
                 f'<p style="color:#888;font-size:13px">Ou copie: {esc(link)}</p>')
        html = es._layout(titulo, f"<p>{esc(corpo)}</p>{botao}")
        texto = f"{corpo}\n\n{link}"
        try:
            from finance import email_inbound as ein
            with pool.connection() as c:
                nome_emp = (c.execute("select nome from contas where id=%s", (conta_id,)).fetchone() or [""])[0]
            if ein.enviar_conta(pool, conta_id, email, titulo, html, texto, from_nome=nome_emp or None):
                return True
        except Exception:  # noqa: BLE001
            pass
        return bool(es.enviar_email(email, titulo, html, texto))
    except Exception:  # noqa: BLE001
        return False


# ------------------------------------------------------------------ inbox
@router.get("/cockpit", response_class=HTMLResponse)
def cockpit_inbox(request: Request):
    sess = _sessao(request)
    if not sess:
        return RedirectResponse("/cockpit/login", status_code=303)
    conta_id, membro_id = sess
    pool = get_pool()
    leads = ck.leads_do_vendedor(pool, conta_id, membro_id)
    p = ck.perfil(pool, conta_id, membro_id)
    vez = sum(1 for l in leads if not l["ia"])
    cards = []
    for l in leads:
        chip = ("<span class='cchip ia'>🤖 IA</span>" if l["ia"]
                else "<span class='cchip voce'>🙋 sua vez</span>")
        cards.append(
            f"<a class=lead href='/cockpit/lead/{l['id']}'>"
            f"<span class=dot style='background:{esc(l['temp_cor'])}'></span>"
            f"<span class=av>{esc(_ini(l['empresa']))}</span>"
            f"<span class=mid><span class=top><span class=emp>{esc(l['empresa'])}</span>{chip}</span>"
            f"<span class=snip>{esc(l['snip'])}</span></span></a>")
    if leads:
        lista = "".join(cards)
    else:
        lista = ("<div class=vazio><div class=big>🎯</div><b>Fila zerada!</b>"
                 "Nenhum lead aberto agora. Quando cair um novo no rodízio, você é avisado.</div>")
    body = (
        "<div class=hdr>"
        f"<a class=ib href='/cockpit/perfil' title='Meu perfil'>{esc(_ini(p['nome']))}</a>"
        f"<div class=tt><b>Meus leads</b><small>{len(leads)} abertos · {vez} sua vez</small></div></div>"
        "<div class=scroll>"
        "<div class=instpill>📲 <b>Instalar o app</b> — no menu do navegador, “Adicionar à tela inicial”.</div>"
        f"{lista}</div>")
    return _page("Cockpit — meus leads", body)


# ------------------------------------------------------------------ detalhe
def _flash(request: Request) -> str:
    ok = request.session.pop("ck_ok", None)
    err = request.session.pop("ck_err", None)
    if ok:
        return f"<div class='msg ok'>{esc(ok)}</div>"
    if err:
        return f"<div class='msg err'>{esc(err)}</div>"
    return ""


@router.get("/cockpit/lead/{lead_id}", response_class=HTMLResponse)
def cockpit_lead(request: Request, lead_id: int):
    sess = _sessao(request)
    if not sess:
        return RedirectResponse("/cockpit/login", status_code=303)
    conta_id, membro_id = sess
    d = ck.lead_do_vendedor(get_pool(), conta_id, membro_id, lead_id)
    if not d:
        return RedirectResponse("/cockpit", status_code=303)
    sub = " · ".join(x for x in [d.get("cidade") or "", (d.get("uf") or "")] if x) or (d.get("cnpj") or "")
    tel = d.get("tel_link") or ""
    zap = d.get("zap_link") or ""
    ficha_btns = (
        (f"<a class=fbtn href='{esc(tel)}'>📞 Ligar</a>" if tel else "<span class=fbtn style='opacity:.4'>📞 Ligar</span>")
        + (f"<a class=fbtn href='{esc(zap)}' target=_blank rel=noopener>💬 WhatsApp</a>" if zap
           else "<span class=fbtn style='opacity:.4'>💬 WhatsApp</span>")
        + f"<a class=fbtn href='/cockpit/lead/{lead_id}/ficha'>👤 Ficha</a>")
    # funil (chips = forms)
    chips = []
    for e in d["etapas"]:
        on = " on" if d["status"] == e["chave"] else ""
        chips.append(
            f"<form class=stf method=post action='/cockpit/lead/{lead_id}/etapa'>"
            f"<input type=hidden name=etapa value='{esc(e['chave'])}'>"
            f"<button class='st{on}' type=submit>{esc(e['rotulo'])}</button></form>")
    perda_opts = "".join(f"<option>{esc(m)}</option>" for m in
                         ("Preço", "Sem retorno", "Comprou concorrente", "Fora do perfil", "Sem interesse"))
    fechar = (
        "<div class=wl>"
        f"<form class=stf method=post action='/cockpit/lead/{lead_id}/fechar'>"
        "<input type=hidden name=tipo value=ganho><button class=win type=submit>✓ Ganho</button></form>"
        "</div>"
        "<details class=perda><summary>✕ Marcar como perdido</summary>"
        f"<form method=post action='/cockpit/lead/{lead_id}/fechar'>"
        "<input type=hidden name=tipo value=perdido>"
        f"<select name=motivo><option value=''>Motivo (opcional)</option>{perda_opts}</select>"
        "<button class=conf type=submit>Confirmar perda</button></form></details>")
    # chat
    bolhas = []
    for m in d["mensagens"]:
        who = m["who"]
        rot = ("<div class=who>🤖 Agente</div>" if who == "ia"
               else "<div class=who>Você</div>" if who == "out" else "")
        cls = "ia" if who == "ia" else ("out" if who == "out" else "in")
        bolhas.append(f"<div class='bub {cls}'>{rot}{esc(m['texto'])}</div>")
    if d["ia"]:
        bolhas.insert(0, "<div class=iabar>🤖 O agente está atendendo. Toque em <b>Assumir</b> pra responder você.</div>")
    chat = "".join(bolhas) or "<div class=iabar>Sem mensagens ainda.</div>"
    if d["ia"]:
        acao = (f"<form method=post action='/cockpit/lead/{lead_id}/assumir'>"
                "<button class=assumir type=submit>🙋 Assumir a conversa (tirar do 🤖 automático)</button></form>")
    else:
        acao = (f"<form class=composer method=post action='/cockpit/lead/{lead_id}/mensagem'>"
                "<input name=texto placeholder='Responder…' required autocomplete=off>"
                "<button type=submit>➤</button></form>")
    body = (
        "<div class=hdr><a class=bk href='/cockpit'>‹</a>"
        f"<div class=tt><b>{esc(d['empresa'])}</b><small>{esc(sub)}</small></div>"
        + ("<span class='cchip ia'>🤖 IA</span>" if d["ia"] else "<span class='cchip voce'>🙋 você</span>")
        + "</div>"
        + _flash(request)
        + f"<div class=ficha>{ficha_btns}</div>"
        + f"<div class=funil><div class=lbl>Etapa no funil</div><div class=stchips>{''.join(chips)}</div>{fechar}</div>"
        + f"<div class=chat>{chat}</div>"
        + acao)
    return _page(f"Cockpit — {d['empresa']}", body)


@router.get("/cockpit/lead/{lead_id}/ficha", response_class=HTMLResponse)
def cockpit_ficha(request: Request, lead_id: int):
    sess = _sessao(request)
    if not sess:
        return RedirectResponse("/cockpit/login", status_code=303)
    conta_id, membro_id = sess
    d = ck.lead_do_vendedor(get_pool(), conta_id, membro_id, lead_id)
    if not d:
        return RedirectResponse("/cockpit", status_code=303)

    def row(lbl, val):
        return f"<div class=frow><span>{esc(lbl)}</span><b>{esc(val)}</b></div>" if val else ""
    empresa_sec = "".join([
        row("Nome", d.get("empresa")), row("CNPJ", d.get("cnpj")),
        row("Segmento", d.get("segmento")),
        row("Cidade", " ".join(x for x in [d.get("cidade") or "", (("/" + d.get("uf")) if d.get("uf") else "")] if x)),
        row("Origem", d.get("origem")),
    ])
    decisor = d.get("decisor_nome") or d.get("contato") or d.get("socio")
    cargo = d.get("decisor_cargo") or d.get("cargo")
    decisor_sec = row("Nome", decisor) + row("Cargo", cargo)
    tels = []
    for numero, wa in [(d.get("whatsapp"), True), (d.get("telefone"), False),
                       (d.get("decisor_telefone"), d.get("decisor_whatsapp"))]:
        if numero:
            selo = "<span class=z>WhatsApp</span>" if wa else ""
            tels.append(f"<div class=tel>📱 {esc(numero)} {selo}</div>")
    tels_sec = "".join(dict.fromkeys(tels))    # dedup preservando ordem
    body = (
        "<div class=hdr>"
        f"<a class=bk href='/cockpit/lead/{lead_id}'>‹</a>"
        f"<div class=tt><b>{esc(d['empresa'])}</b><small>ficha do lead</small></div></div>"
        "<div class=scroll>"
        + (f"<div class=fsec><div class=lbl>Empresa</div>{empresa_sec}</div>" if empresa_sec else "")
        + (f"<div class=fsec><div class=lbl>Decisor</div>{decisor_sec}</div>" if decisor_sec else "")
        + (f"<div class=fsec><div class=lbl>Telefones</div>{tels_sec}</div>" if tels_sec else "")
        + f"<div class=fsec><div class=lbl>Funil</div>{row('Etapa atual', d.get('status'))}</div>"
        + (f"<div class=fsec><div class=lbl>Observações</div><div style='font-size:.88rem;color:var(--mut)'>{esc(d.get('obs'))}</div></div>" if d.get("obs") else "")
        + "</div>")
    return _page(f"Cockpit — ficha {d['empresa']}", body)


# ------------------------------------------------------------------ ações (POST)
def _acao(request: Request, lead_id: int, fn) -> RedirectResponse:
    sess = _sessao(request)
    if not sess:
        return RedirectResponse("/cockpit/login", status_code=303)
    conta_id, membro_id = sess
    r = fn(get_pool(), conta_id, membro_id, lead_id)
    if r.get("ok"):
        request.session["ck_ok"] = r.get("msg", "Feito ✓")
    else:
        request.session["ck_err"] = r.get("erro", "Não deu certo.")
    return RedirectResponse(f"/cockpit/lead/{lead_id}", status_code=303)


@router.post("/cockpit/lead/{lead_id}/mensagem")
def cockpit_mensagem(request: Request, lead_id: int, texto: str = Form(...)):
    return _acao(request, lead_id, lambda p, c, m, l: {**ck.enviar_mensagem(p, c, m, l, texto),
                                                       "msg": "Mensagem enviada ✓"})


@router.post("/cockpit/lead/{lead_id}/etapa")
def cockpit_etapa(request: Request, lead_id: int, etapa: str = Form(...)):
    return _acao(request, lead_id, lambda p, c, m, l: {**ck.mudar_etapa(p, c, m, l, etapa),
                                                       "msg": "Etapa atualizada ✓"})


@router.post("/cockpit/lead/{lead_id}/assumir")
def cockpit_assumir(request: Request, lead_id: int):
    return _acao(request, lead_id, lambda p, c, m, l: {**ck.assumir(p, c, m, l),
                                                       "msg": "Você assumiu a conversa ✓"})


@router.post("/cockpit/lead/{lead_id}/fechar")
def cockpit_fechar(request: Request, lead_id: int, tipo: str = Form(...), motivo: str = Form("")):
    sess = _sessao(request)
    if not sess:
        return RedirectResponse("/cockpit/login", status_code=303)
    conta_id, membro_id = sess
    r = ck.fechar(get_pool(), conta_id, membro_id, lead_id, tipo, motivo)
    if r.get("ok"):
        request.session["ck_ok"] = "🎉 Marcado como Ganho!" if tipo == "ganho" else "Marcado como Perdido."
        return RedirectResponse("/cockpit", status_code=303)     # saiu da fila
    request.session["ck_err"] = r.get("erro", "Não deu certo.")
    return RedirectResponse(f"/cockpit/lead/{lead_id}", status_code=303)


# ------------------------------------------------------------------ perfil
@router.get("/cockpit/perfil", response_class=HTMLResponse)
def cockpit_perfil(request: Request):
    sess = _sessao(request)
    if not sess:
        return RedirectResponse("/cockpit/login", status_code=303)
    conta_id, membro_id = sess
    p = ck.perfil(get_pool(), conta_id, membro_id)

    def tgl(label, sub, on, action):
        cls = "tgl on" if on else "tgl"
        txt = "Ligado" if on else "Desligado"
        return (f"<div class=prow><div>{esc(label)}<div class=sub>{esc(sub)}</div></div>"
                f"<form method=post action='{action}'><input type=hidden name=on value='{0 if on else 1}'>"
                f"<button class='{cls}' type=submit>{txt}</button></form></div>")
    body = (
        "<div class=hdr><a class=bk href='/cockpit'>‹</a>"
        "<div class=tt><b>Meu perfil</b><small>vendedor</small></div></div>"
        "<div class=scroll>"
        f"<div class=pcard><div class=av>{esc(_ini(p['nome']))}</div>"
        f"<div><b>{esc(p['nome'])}</b><small>{esc(p['email'])}{(' · ' + esc(p['whatsapp'])) if p['whatsapp'] else ''}</small></div></div>"
        "<div class=kpis>"
        f"<div class=kpi><b>{p['na_fila']}</b><small>na fila</small></div>"
        f"<div class=kpi><b>{p['ganhos']}</b><small>ganhos no mês</small></div>"
        f"<div class=kpi><b>{p['atendidos']}</b><small>atendidos</small></div></div>"
        + tgl("Notificações push", "avisar quando cair um lead", p["push_ativo"], "/cockpit/perfil/push")
        + tgl("Receber no rodízio", "desligue pra pausar leads novos", not p["pausado"], "/cockpit/perfil/rodizio")
        + "<a class=sair href='/cockpit/sair' style='display:block;text-align:center'>Sair</a>"
        + "</div>")
    return _page("Cockpit — perfil", body)


@router.post("/cockpit/perfil/push")
def cockpit_perfil_push(request: Request, on: str = Form("1")):
    sess = _sessao(request)
    if not sess:
        return RedirectResponse("/cockpit/login", status_code=303)
    ck.set_push(get_pool(), sess[0], sess[1], on == "1")
    return RedirectResponse("/cockpit/perfil", status_code=303)


@router.post("/cockpit/perfil/rodizio")
def cockpit_perfil_rodizio(request: Request, on: str = Form("1")):
    # o toggle mostra "receber no rodízio"; pausado = NÃO receber → inverte
    sess = _sessao(request)
    if not sess:
        return RedirectResponse("/cockpit/login", status_code=303)
    ck.set_pausado(get_pool(), sess[0], sess[1], on != "1")
    return RedirectResponse("/cockpit/perfil", status_code=303)


# ------------------------------------------------------------------ PWA (manifest, SW, ícone)
_ICON_SVG = (
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 512 512'>"
    "<rect width='512' height='512' rx='96' fill='#0e0e0f'/>"
    "<rect x='40' y='40' width='432' height='432' rx='80' fill='#10241a'/>"
    "<path d='M170 150 h150 L190 362 h150' stroke='#5dcaa5' stroke-width='34' "
    "fill='none' stroke-linecap='round' stroke-linejoin='round'/></svg>")


@router.get("/cockpit/icon.svg", include_in_schema=False)
def cockpit_icon():
    return Response(_ICON_SVG, media_type="image/svg+xml",
                   headers={"Cache-Control": "public, max-age=604800"})


@router.get("/cockpit/manifest.webmanifest", include_in_schema=False)
def cockpit_manifest():
    import json
    m = {
        "name": "Cockpit do Vendedor", "short_name": "Cockpit",
        "start_url": "/cockpit", "scope": "/cockpit", "display": "standalone",
        "background_color": "#0e0e0f", "theme_color": "#0e0e0f",
        "description": "Receba e atenda seus leads.",
        "icons": [
            {"src": "/cockpit/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any maskable"},
        ],
    }
    return Response(json.dumps(m), media_type="application/manifest+json",
                   headers={"Cache-Control": "public, max-age=86400"})


_SW = """
const CACHE='cockpit-v1';
self.addEventListener('install',e=>{self.skipWaiting();});
self.addEventListener('activate',e=>{e.waitUntil(self.clients.claim());});
self.addEventListener('fetch',e=>{
  const r=e.request; if(r.method!=='GET'){return;}
  e.respondWith(fetch(r).then(res=>{
    try{const cp=res.clone();caches.open(CACHE).then(c=>c.put(r,cp));}catch(_){}
    return res;
  }).catch(()=>caches.match(r)));
});
// push chega na PRÓXIMA fase; handlers já prontos pra não quebrar quando ligar.
self.addEventListener('push',e=>{
  let d={title:'Novo lead',body:'Toque pra atender'};
  try{d=Object.assign(d,e.data.json());}catch(_){}
  e.waitUntil(self.registration.showNotification(d.title,{body:d.body,icon:'/cockpit/icon.svg',
    badge:'/cockpit/icon.svg',data:{url:d.url||'/cockpit'}}));
});
self.addEventListener('notificationclick',e=>{
  e.notification.close();
  e.waitUntil(clients.matchAll({type:'window'}).then(ws=>{
    for(const w of ws){if(w.url.includes('/cockpit')&&'focus'in w)return w.focus();}
    return clients.openWindow((e.notification.data&&e.notification.data.url)||'/cockpit');
  }));
});
"""


@router.get("/cockpit/sw.js", include_in_schema=False)
def cockpit_sw():
    return Response(_SW, media_type="application/javascript",
                   headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/cockpit"})


# ------------------------------------------------------------------ gestor: gerar link
@router.post("/painel/equipe/cockpit-link")
def painel_equipe_cockpit_link(request: Request, membro_id: int = Form(...)):
    from web.portal import conta_logada
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    if request.session.get("papel", "dono") != "dono":
        return RedirectResponse("/painel", status_code=303)
    pool = get_pool()
    with pool.connection() as c:
        m = c.execute("select coalesce(nullif(nome,''), email), email, papel, ativo "
                      "from membros where id=%s and conta_id=%s", (membro_id, conta[0])).fetchone()
    if not m or not m[3] or (m[2] or "") not in _PAPEIS_OK:
        request.session["equipe_erro"] = "Só dá pra gerar o link do Cockpit pra vendedor/gestor ativo."
        return RedirectResponse("/painel/equipe", status_code=303)
    token = ck.gerar_token(pool, conta[0], membro_id)
    link = ck.link_acesso(token)
    request.session["equipe_link"] = link
    request.session["equipe_link_cap"] = "🔗 Link do Cockpit gerado — mande pra pessoa (vale 15 min):"
    enviado = False
    if m[1] and "@" in (m[1] or ""):
        enviado = _enviar_link_email(pool, conta[0], m[1], link)
    request.session["equipe_aviso"] = (
        f"Link do Cockpit gerado e enviado por e-mail para {m[1]} ✓ (vale 15 min)."
        if enviado else
        "Link do Cockpit gerado abaixo — mande pra pessoa (vale 15 min).")
    return RedirectResponse("/painel/equipe", status_code=303)
