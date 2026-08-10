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

from fastapi import APIRouter, Request, Form, Body
from fastapi.responses import HTMLResponse, RedirectResponse, Response, JSONResponse

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


def _gerencia(request: Request):
    """(conta_id, membro_id) se dono/gestor — pro Cockpit do Dono. NÃO exige membro_id:
    o dono logado no painel tem conta_id+papel mas não tem membro_id (é o titular), e
    a visão de equipe é toda por conta_id. Assim ele entra pela sessão do painel, sem
    precisar do login por link mágico. membro_id vem None pro dono, id pro gestor."""
    cid = request.session.get("conta_id")
    papel = request.session.get("papel", "dono")
    if not cid or papel not in ("dono", "gestor"):
        return None
    return cid, request.session.get("membro_id")


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
.fbtn.orc{border-color:#1e4a3a;color:var(--verde-claro);background:#10241a;font-weight:700}
.orc-srv{display:flex;align-items:center;gap:.6rem;padding:.6rem 1rem;border-bottom:1px solid var(--borda);cursor:pointer}
.orc-srv .ck{width:22px;height:22px;border-radius:6px;border:1.5px solid var(--borda);flex-shrink:0;display:grid;place-items:center;font-size:.8rem;color:#04140d}
.orc-srv.on .ck{background:var(--verde);border-color:var(--verde)}
.orc-srv .m{flex:1;min-width:0}.orc-srv .m b{font-size:.9rem;display:block}.orc-srv .m small{color:var(--mut);font-size:.76rem}
.orc-srv .pr{font-size:.8rem;color:var(--verde-claro);text-align:right;flex-shrink:0}
.orc-q{display:flex;align-items:center;gap:.4rem;margin-top:.3rem}
.orc-q button{width:22px;height:22px;border-radius:6px;border:1px solid var(--borda);background:var(--card2);color:var(--txt);font-size:.9rem;cursor:pointer}
.orc-q span{font-size:.82rem;min-width:14px;text-align:center}
.orc-add{margin:.7rem 1rem;display:flex;gap:.4rem}
.orc-add input{background:var(--card2);border:1px solid var(--borda);border-radius:8px;color:var(--txt);padding:.5rem .6rem;font-size:.82rem;min-width:0;font-family:inherit}
.orc-add input.n{flex:1}.orc-add input.v{width:82px}
.orc-add button{background:var(--card2);border:1px solid var(--borda);border-radius:8px;color:var(--verde-claro);padding:0 .8rem;font-weight:700;cursor:pointer}
.orc-foot{border-top:1px solid var(--borda);padding:.7rem 1rem;background:var(--bg);flex-shrink:0}
.orc-tot{display:flex;justify-content:space-between;font-size:.92rem;margin-bottom:.5rem}.orc-tot b{color:var(--verde-claro)}
.orc-gen{width:100%;background:var(--verde);color:#04140d;border:0;border-radius:10px;padding:.72rem;font-weight:700;font-size:.92rem;cursor:pointer}
.orc-gen:disabled{opacity:.4}
.orc-done{padding:1.5rem 1.1rem;text-align:center}.orc-done .big{font-size:2.2rem}.orc-done h3{margin:.4rem 0 .2rem}.orc-done p{color:var(--mut);font-size:.85rem}
.orc-link{display:flex;gap:.4rem;margin:1rem 0 .6rem}
.orc-link input{flex:1;min-width:0;background:var(--card2);border:1px solid var(--borda);border-radius:8px;color:var(--verde-claro);padding:.55rem .6rem;font-size:.74rem}
.orc-link button{background:var(--card2);border:1px solid var(--borda);border-radius:8px;color:var(--txt);padding:0 .8rem;font-weight:700;cursor:pointer}
.wpp{width:100%;background:var(--zap);color:#052b12;border:0;border-radius:10px;padding:.72rem;font-weight:700;font-size:.92rem;cursor:pointer;margin-top:.3rem;display:block;text-align:center;text-decoration:none}
.orc-ghost{width:100%;background:none;border:1px solid var(--borda);color:var(--mut);border-radius:10px;padding:.6rem;font-weight:600;cursor:pointer;margin-top:.5rem}
.orc-empty{padding:2rem 1.4rem;text-align:center;color:var(--mut);font-size:.86rem}
.fbtn.vis{border-color:#1e3a52;color:#7bb8e6;background:#0f1d2b;font-weight:700}
.vsec{padding:.8rem 1rem;border-bottom:1px solid var(--borda)}
.vchips{display:flex;gap:.4rem;flex-wrap:wrap}
.vtimes{display:grid;grid-template-columns:repeat(4,1fr);gap:.4rem}
.vchip{font-size:.82rem;padding:.42rem .7rem;border-radius:999px;border:1px solid var(--borda);background:var(--card2);color:var(--mut);cursor:pointer;text-align:center}
.vchip.on{border-color:var(--verde);background:#10241a;color:var(--verde-claro);font-weight:700}
.vlocal{background:var(--card2);border:1px solid var(--borda);border-radius:10px;padding:.6rem .7rem}
.vlocal .nome{font-weight:700;font-size:.88rem}.vlocal .end{color:var(--mut);font-size:.8rem;margin-top:.15rem}
.vlocal a{color:#7bb8e6;font-size:.78rem;font-weight:600}
.vlocal input{width:100%;margin-top:.4rem;background:var(--bg);border:1px solid var(--borda);border-radius:8px;color:var(--txt);padding:.5rem .6rem;font-size:.84rem;font-family:inherit}
.vvisit{background:#0f1d2b;border:1px solid #1e3a52;border-radius:10px;padding:.6rem .7rem;font-size:.84rem}
.vvisit b{color:#cfe6dd}.vvisit small{color:var(--mut);display:block;font-size:.74rem;margin-top:.15rem}
.vrow{display:flex;align-items:center;justify-content:space-between;gap:1rem}.vrow .sub{color:var(--mut);font-size:.76rem}
.vmsg{margin-top:.6rem;background:#0c1c10;border:1px solid #16391f;border-radius:10px;padding:.6rem .7rem;font-size:.8rem;color:#cfe6dd;white-space:pre-line}
/* ---- Cockpit do Dono (prefixo d) ---- */
.dseg{display:flex;gap:.3rem;padding:.5rem 1rem;border-bottom:1px solid var(--borda)}
.dseg a{font-size:.72rem;padding:.25rem .6rem;border-radius:999px;border:1px solid var(--borda);color:var(--mut);text-decoration:none}
.dseg a.on{border-color:var(--verde);background:#10241a;color:var(--verde-claro);font-weight:700}
.dkpis{display:grid;grid-template-columns:1fr 1fr;gap:.5rem;padding:.8rem}
.dkpi{background:var(--card2);border:1px solid var(--borda);border-radius:12px;padding:.7rem .8rem}
.dkpi.hl{background:#10241a;border-color:#1e4a3a}
.dkpi .v{font-size:1.3rem;font-weight:800}.dkpi .l{color:var(--mut);font-size:.72rem}.dkpi .d{font-size:.68rem;color:var(--mut);margin-top:.15rem}
.dsect{padding:.5rem 1rem .2rem;font-size:.82rem;font-weight:700;display:flex;justify-content:space-between;align-items:center}
.dsect span{color:var(--mut);font-weight:400;font-size:.72rem}
.dfunil{padding:.3rem 1rem .8rem;display:flex;flex-direction:column;gap:.45rem}
.dfr{display:flex;align-items:center;gap:.6rem;font-size:.8rem}
.dfr .nm{width:76px;color:var(--mut);flex-shrink:0}
.dfr .bar{flex:1;height:15px;background:var(--card2);border-radius:6px;overflow:hidden;border:1px solid var(--borda)}
.dfr .bar i{display:block;height:100%;background:linear-gradient(90deg,#1d9e75,#5dcaa5)}
.dfr .qt{width:82px;text-align:right;flex-shrink:0}.dfr .qt b{color:var(--txt)}.dfr .qt small{color:var(--mut)}
.daten{padding:.2rem 1rem 1rem;display:grid;grid-template-columns:1fr 1fr;gap:.5rem}
.dat{border:1px solid var(--borda);background:var(--card2);border-radius:11px;padding:.6rem .7rem}
.dat .n{font-size:1.3rem;font-weight:800}.dat .t{font-size:.72rem;color:var(--mut);line-height:1.3}
.dat.warn{border-color:#5a4520;background:#241c0f}.dat.warn .n{color:var(--amar)}
.dat.hot{border-color:#5a2b2b;background:#241313}.dat.hot .n{color:#f0917f}
.dat.info{border-color:#1e3a52;background:#0f1d2b}.dat.info .n{color:#7bb8e6}
.dvend{display:flex;align-items:center;gap:.7rem;padding:.7rem 1rem;border-bottom:1px solid var(--borda);color:var(--txt);text-decoration:none}
.dvend .rk{width:22px;text-align:center;font-weight:800;color:var(--mut);flex-shrink:0}
.dvend .av{width:38px;height:38px;border-radius:50%;background:#22252a;color:#cfe6dd;display:grid;place-items:center;font-weight:700;flex-shrink:0}
.dvend .mid{flex:1;min-width:0}.dvend .mid b{font-size:.9rem}
.dvend .mid .sub{color:var(--mut);font-size:.73rem;display:flex;gap:.55rem;flex-wrap:wrap}
.dvend .rt{text-align:right;flex-shrink:0}.dvend .rt .g{font-weight:800;color:var(--verde-claro)}.dvend .rt small{color:var(--mut);font-size:.68rem;display:block}
.dpaus{font-size:.62rem;color:var(--amar);border:1px solid #5a4520;background:#241c0f;border-radius:999px;padding:.02rem .35rem}
.dev{display:flex;gap:.6rem;padding:.6rem 1rem;border-bottom:1px solid var(--borda)}
.dev .ic{width:30px;height:30px;border-radius:8px;display:grid;place-items:center;flex-shrink:0;font-size:.9rem;background:var(--card2);border:1px solid var(--borda)}
.dev.ganho .ic{background:#10241a;border-color:#1e4a3a}.dev.visita .ic{background:#0f1d2b;border-color:#1e3a52}
.dev.prop .ic{background:#1a1226;border-color:#3a2b52}.dev.perdido .ic{background:#241313;border-color:#5a2b2b}
.dev .tx{flex:1;font-size:.84rem}
.dvstat{display:grid;grid-template-columns:1fr 1fr 1fr;gap:.5rem;padding:.8rem}
.dvstat .s{background:var(--card2);border:1px solid var(--borda);border-radius:11px;padding:.6rem;text-align:center}
.dvstat .s b{font-size:1.1rem;display:block}.dvstat .s small{color:var(--mut);font-size:.66rem}
.dvline{display:flex;justify-content:space-around;padding:0 1rem .6rem;font-size:.82rem;color:var(--mut)}.dvline b{color:var(--txt)}
.dvact{padding:0 1rem .6rem}
.dvact form{display:inline}
.dvact .pausebtn{width:100%;font:inherit;font-size:.82rem;font-weight:600;border-radius:9px;padding:.55rem;cursor:pointer;border:1px solid var(--borda);background:var(--card2);color:var(--txt)}
.dlead{display:flex;align-items:center;gap:.6rem;padding:.55rem 1rem;border-bottom:1px solid var(--borda)}
.dlead .dot2{width:9px;height:9px;border-radius:50%;flex-shrink:0}
.dlead .m{flex:1;min-width:0}.dlead .m b{font-size:.84rem}
.dlead form{display:flex;gap:.3rem;align-items:center}
.dlead select{background:var(--card2);border:1px solid var(--borda);border-radius:7px;color:var(--txt);font-size:.72rem;padding:.25rem}
.dlead button{background:var(--card2);border:1px solid var(--borda);border-radius:7px;color:var(--verde-claro);font-size:.72rem;padding:.25rem .5rem;font-weight:700;cursor:pointer}
.dnav{display:flex;border-top:1px solid var(--borda);flex-shrink:0;background:var(--bg)}
.dnav a{flex:1;color:var(--mut);font-size:.66rem;padding:.5rem 0;text-decoration:none;display:flex;flex-direction:column;align-items:center;gap:.15rem}
.dnav a .i{font-size:1.15rem}.dnav a.on{color:var(--verde-claro)}
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
/* swipe: card desliza pra revelar ações atrás */
.swipe{position:relative;overflow:hidden;background:var(--bg)}
.swipe .actions{position:absolute;top:0;right:0;height:100%;display:flex}
.swipe .act{width:82px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:.15rem;color:#fff;font-size:.72rem;font-weight:600;border:0;cursor:pointer}
.swipe .act .em{font-size:1.1rem}
.act.assumir{background:linear-gradient(180deg,#7b4fb0,#5f3a90)}
.act.devolver{background:linear-gradient(180deg,#3a2b52,#2a2140);color:var(--roxo)}
.act.ganho{background:linear-gradient(180deg,#1d9e75,#158a63)}
.front{position:relative;z-index:2;background:var(--bg);transition:transform .22s cubic-bezier(.2,.8,.3,1);will-change:transform;touch-action:pan-y}
.front.drag{transition:none}
.grab{margin-left:.3rem;color:#4a4a4c;font-size:.95rem;flex-shrink:0}
.pushcard{margin:.7rem 1rem;border:1px solid #3a2b52;background:#160f22;border-radius:13px;padding:.8rem;display:none}
.pushcard.show{display:block}
.pushcard b{display:block;font-size:.92rem;margin-bottom:.15rem}
.pushcard p{margin:.1rem 0 .6rem;color:var(--mut);font-size:.8rem}
.pushcard .go{background:var(--roxo);color:#1a0f2a;border:0;border-radius:9px;padding:.55rem .8rem;font-weight:700;font-size:.84rem;width:100%;cursor:pointer}
.ck-toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%) translateY(20px);opacity:0;background:#222226;border:1px solid #3a3a3c;color:var(--txt);padding:.5rem .85rem;border-radius:10px;font-size:.83rem;transition:.25s;z-index:99;pointer-events:none;max-width:90vw;text-align:center}
.ck-toast.show{transform:translateX(-50%) translateY(0);opacity:1}
</style>"""

_SW_REG = ("<script>if('serviceWorker' in navigator){"
           "navigator.serviceWorker.register('/cockpit/sw.js').catch(function(){});}</script>")

# JS da caixa de leads: swipe pra ações + ativar push. Plain string (tem chaves de JS).
_INBOX_JS = r"""
<div class="ck-toast" id="cktoast"></div>
<script>
(function(){
  function toast(m){var t=document.getElementById('cktoast');if(!t)return;t.textContent=m;t.classList.add('show');
    clearTimeout(t._t);t._t=setTimeout(function(){t.classList.remove('show');},2000);}
  function actionsHTML(ia){
    var main = ia
      ? '<button class="act assumir" data-a="assumir"><span class="em">🙋</span>Assumir</button>'
      : '<button class="act devolver" data-a="devolver"><span class="em">🤖</span>Devolver</button>';
    return '<div class="actions">'+main+'<button class="act ganho" data-a="ganho"><span class="em">✓</span>Ganho</button></div>';
  }
  var openFront=null;
  function closeF(f){ if(!f)return; f.classList.remove('open'); f.style.transform='translateX(0)'; if(openFront===f)openFront=null; }
  function actW(row){ var a=row.querySelector('.actions'); return a?a.offsetWidth:164; }

  function wire(row){
    var front=row.querySelector('.front');
    var id=front.getAttribute('data-id');
    var sx=0,sy=0,base=0,dragging=false,decided=false,horiz=false,moved=false;
    front.addEventListener('pointerdown',function(e){
      if(openFront&&openFront!==front)closeF(openFront);
      dragging=true;decided=false;horiz=false;moved=false;sx=e.clientX;sy=e.clientY;
      base=front.classList.contains('open')?-actW(row):0;
      front.classList.add('drag');try{front.setPointerCapture(e.pointerId);}catch(_){}
    });
    front.addEventListener('pointermove',function(e){
      if(!dragging)return;var mx=e.clientX-sx,my=e.clientY-sy;
      if(!decided){if(Math.abs(mx)>6||Math.abs(my)>6){decided=true;horiz=Math.abs(mx)>Math.abs(my);}}
      if(!horiz)return; e.preventDefault(); moved=true;
      var w=actW(row); var x=Math.max(-w-20,Math.min(0,base+mx)); front.style.transform='translateX('+x+'px)';
    });
    function end(){ if(!dragging)return;dragging=false;front.classList.remove('drag');
      var m=front.style.transform.match(/-?\d+\.?\d*/); var cur=m?parseFloat(m[0]):0; var w=actW(row);
      if(cur<-w/2){front.classList.add('open');front.style.transform='translateX('+(-w)+'px)';openFront=front;}
      else{closeF(front);}
    }
    front.addEventListener('pointerup',end); front.addEventListener('pointercancel',end);
    front.addEventListener('click',function(){
      if(front.classList.contains('open')){closeF(front);return;}
      if(!moved){ location.href='/cockpit/lead/'+id; }
    });
    row.querySelectorAll('.act').forEach(function(b){
      b.addEventListener('click',function(ev){ ev.stopPropagation(); doAct(row,front,id,b.getAttribute('data-a')); });
    });
  }
  function doAct(row,front,id,a){
    var url = a==='ganho' ? '/cockpit/lead/'+id+'/fechar'
            : a==='devolver' ? '/cockpit/lead/'+id+'/devolver'
            : '/cockpit/lead/'+id+'/assumir';
    var opt={method:'POST',headers:{'x-cockpit':'1'}};
    if(a==='ganho'){opt.headers['Content-Type']='application/x-www-form-urlencoded';opt.body='tipo=ganho';}
    fetch(url,opt).then(function(r){return r.json();}).then(function(j){
      if(!j||!j.ok){toast((j&&j.erro)||'Não deu certo');closeF(front);return;}
      if(a==='ganho'){ row.style.height=row.offsetHeight+'px';row.style.transition='.25s';
        requestAnimationFrame(function(){row.style.height='0';row.style.opacity='0';});
        setTimeout(function(){row.remove();},260); toast('🎉 Marcado como Ganho'); return; }
      var ia = (a!=='assumir'); // assumir→sem IA; devolver→com IA
      var tmp=document.createElement('div'); tmp.innerHTML=actionsHTML(ia);
      row.querySelector('.actions').replaceWith(tmp.firstChild);   // troca as ações (assumir↔devolver)
      var chip=front.querySelector('.cchip');
      if(chip){ chip.className='cchip '+(ia?'ia':'voce'); chip.textContent=ia?'🤖 IA':'🙋 sua vez'; }
      front.setAttribute('data-ia', ia?'1':'0');
      row.querySelectorAll('.act').forEach(function(b){
        b.addEventListener('click',function(ev){ev.stopPropagation();doAct(row,front,id,b.getAttribute('data-a'));});
      });
      closeF(front);
      toast(a==='assumir'?'🙋 Agente desativado — é a sua vez':'🤖 Devolvido pro agente');
    }).catch(function(){toast('Falha de conexão');closeF(front);});
  }
  document.querySelectorAll('.swipe').forEach(wire);

  // ---- push ----
  function urlB64(s){var p='='.repeat((4-s.length%4)%4);var b=atob((s+p).replace(/-/g,'+').replace(/_/g,'/'));
    var a=new Uint8Array(b.length);for(var i=0;i<b.length;i++)a[i]=b.charCodeAt(i);return a;}
  function subscribe(){
    if(!('serviceWorker'in navigator)||!('PushManager'in window)||!window.CKVAPID)return Promise.resolve(false);
    return navigator.serviceWorker.ready.then(function(reg){
      return reg.pushManager.getSubscription().then(function(s){
        if(s)return s;
        return reg.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:urlB64(window.CKVAPID)});
      });
    }).then(function(sub){
      return fetch('/cockpit/push/assinar',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(sub)});
    }).then(function(){return true;}).catch(function(){return false;});
  }
  var card=document.getElementById('pushcard');
  if(card && ('Notification'in window) && window.CKVAPID){
    if(Notification.permission==='granted'){ subscribe(); }
    else if(Notification.permission!=='denied'){
      card.classList.add('show');
      document.getElementById('pushbtn').addEventListener('click',function(){
        Notification.requestPermission().then(function(p){
          if(p==='granted'){ subscribe().then(function(){toast('🔔 Notificações ligadas');}); }
          else { toast('Você pode ligar depois no navegador'); }
          card.classList.remove('show');
        });
      });
    }
  }
})();
</script>"""

# JS do montador de orçamento (plain string — chaves de JS).
_ORC_JS = r"""
<script>
(function(){
  var O=window.ORC||{cat:[],leadId:0};
  var sel={}, custom=[];
  function $(id){return document.getElementById(id);}
  function brl(v){return "R$ "+Number(v||0).toLocaleString("pt-BR");}
  function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(m){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[m];});}
  function toast(m){var t=$("cktoast");if(!t)return;t.textContent=m;t.classList.add("show");clearTimeout(t._t);t._t=setTimeout(function(){t.classList.remove("show");},2000);}
  function pr(s){var a=[];if(s.setup)a.push(brl(s.setup));if(s.mensal)a.push(brl(s.mensal)+"/mês");return a.join(" + ")||"grátis";}

  function renderCat(){
    var box=$("orclist");if(!box)return;box.innerHTML="";
    if(!O.cat.length){box.innerHTML='<div class=orc-empty>Nenhum serviço no catálogo ainda. Você pode adicionar itens avulsos abaixo, ou pedir pro gestor cadastrar o catálogo no painel.</div>';return;}
    O.cat.forEach(function(s,i){
      var on=sel[i]!==undefined;
      var q=on?'<div class=orc-q><button data-q="-" data-i="'+i+'">−</button><span>'+sel[i]+'</span><button data-q="+" data-i="'+i+'">+</button></div>':'';
      var d=document.createElement("div");d.className="orc-srv"+(on?" on":"");d.setAttribute("data-i",i);
      d.innerHTML='<div class=ck>'+(on?'✓':'')+'</div><div class=m><b>'+esc(s.nome)+'</b>'+(s.desc?'<small>'+esc(s.desc)+'</small>':'')+q+'</div><div class=pr>'+pr(s)+'</div>';
      box.appendChild(d);
    });
  }
  function build(){
    var out=[];
    Object.keys(sel).forEach(function(i){var s=O.cat[i];var q=sel[i];
      out.push({nome:s.nome+(q>1?" (× "+q+")":""),setup:(s.setup||0)*q,mensal:(s.mensal||0)*q});});
    custom.forEach(function(c){out.push(c);});
    return out;
  }
  function total(){
    var its=build();var setup=0,mensal=0;its.forEach(function(x){setup+=x.setup;mensal+=x.mensal;});
    var t=[];if(setup)t.push(brl(setup));if(mensal)t.push(brl(mensal)+"/mês");
    $("orctotal").innerHTML=its.length?'<b>'+(t.join(" + ")||"grátis")+'</b>':"—";
    $("orcgen").disabled=its.length===0;
  }
  document.addEventListener("click",function(e){
    var qb=e.target.closest("[data-q]");
    if(qb){e.stopPropagation();var i=qb.getAttribute("data-i");sel[i]=Math.max(1,(sel[i]||1)+(qb.getAttribute("data-q")==="+"?1:-1));renderCat();total();return;}
    var row=e.target.closest(".orc-srv");
    if(row){var i=row.getAttribute("data-i");if(sel[i]!==undefined)delete sel[i];else sel[i]=1;renderCat();total();}
  });
  var addb=$("orcaddbtn");
  if(addb)addb.onclick=function(){var n=$("orccnome").value.trim();var v=parseInt(($("orccval").value||"").replace(/\D/g,''),10);
    if(!n||!v){toast("Preencha nome e valor");return;}custom.push({nome:n,setup:v,mensal:0});$("orccnome").value="";$("orccval").value="";total();toast("Item adicionado ✓");};
  var gen=$("orcgen");
  if(gen)gen.onclick=function(){
    gen.disabled=true;gen.textContent="Gerando…";
    fetch("/cockpit/lead/"+O.leadId+"/orcamento",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({itens:build()})})
      .then(function(r){return r.json();}).then(function(j){
        if(!j||!j.ok){toast((j&&j.erro)||"Não deu certo");gen.disabled=false;gen.textContent="Gerar proposta e link";return;}
        done(j);
      }).catch(function(){toast("Falha de conexão");gen.disabled=false;gen.textContent="Gerar proposta e link";});
  };
  function done(j){
    $("orcbuild").style.display="none";$("orcfoot").style.display="none";
    var wa=j.zap?'<a class=wpp href="'+j.zap+'" target=_blank rel=noopener>💬 Enviar no WhatsApp do cliente</a>':'';
    $("orcdone").innerHTML='<div class=orc-done><div class=big>🧾✅</div><h3>Proposta pronta!</h3>'
      +'<p>Mande o link pro cliente — ele abre, vê com a marca da empresa e aprova.</p>'
      +'<div class=orc-link><input value="'+esc(j.link)+'" readonly onclick="this.select()"><button id=orccopy>Copiar</button></div>'
      +wa
      +'<button class=orc-ghost id=orcsend>💬 Enviar na conversa (pelo Zaq)</button>'
      +'<a class=orc-ghost href="/cockpit/lead/'+O.leadId+'" style="display:block;border:0;color:var(--verde-claro);text-decoration:none">✓ Voltar pro lead</a></div>';
    $("orcdone").style.display="block";
    $("orccopy").onclick=function(){navigator.clipboard.writeText(j.link).then(function(){toast("Link copiado ✓");},function(){toast("Selecione e copie");});};
    $("orcsend").onclick=function(){var b=$("orcsend");b.disabled=true;b.textContent="Enviando…";
      fetch("/cockpit/lead/"+O.leadId+"/orcamento/enviar",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({link:j.link})})
        .then(function(r){return r.json();}).then(function(x){toast(x&&x.ok?"Enviado na conversa ✓":(x&&x.erro)||"Não consegui enviar agora");b.disabled=false;b.textContent="💬 Enviar na conversa (pelo Zaq)";})
        .catch(function(){toast("Falha de conexão");b.disabled=false;b.textContent="💬 Enviar na conversa (pelo Zaq)";});};
  }
  renderCat();total();
})();
</script>"""

# JS do agendar visita (plain string).
_VISITA_JS = r"""
<script>
(function(){
  var V=window.VISITA||{leadId:0,dias:[],horas:[],end:""};
  var st={dia:(V.dias[0]||{}).iso,hora:"10:00",dur:60,lembr:60,avisar:true};
  function $(id){return document.getElementById(id);}
  function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(m){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[m];});}
  function toast(m){var t=$("cktoast");if(!t)return;t.textContent=m;t.classList.add("show");clearTimeout(t._t);t._t=setTimeout(function(){t.classList.remove("show");},2000);}
  var DURS=[["30 min",30],["1 h",60],["1h30",90],["2 h",120]];
  var LEMBR=[["Sem lembrete",0],["1h antes",60],["1 dia antes",1440]];

  function chip(lab,on){return '<div class="vchip'+(on?' on':'')+'">'+lab+'</div>';}
  function render(){
    $("vdays").innerHTML=V.dias.map(function(d){return '<div class="vchip'+(d.iso===st.dia?' on':'')+'" data-d="'+d.iso+'">'+d.lab+'</div>';}).join("");
    $("vtimes").innerHTML=V.horas.map(function(h){return '<div class="vchip'+(h===st.hora?' on':'')+'" data-h="'+h+'">'+h+'</div>';}).join("");
    $("vdurs").innerHTML=DURS.map(function(o){return '<div class="vchip'+(o[1]===st.dur?' on':'')+'" data-dur="'+o[1]+'">'+o[0]+'</div>';}).join("");
    $("vlembr").innerHTML=LEMBR.map(function(o){return '<div class="vchip'+(o[1]===st.lembr?' on':'')+'" data-lb="'+o[1]+'">'+o[0]+'</div>';}).join("");
    prev();
  }
  function diaLab(){var d=V.dias.filter(function(x){return x.iso===st.dia;})[0];return d?d.lab.toLowerCase():st.dia;}
  function prev(){
    var on=!$("vavisar").classList.contains("off");
    $("vmsg").style.display=on?"block":"none";
    $("vmsg").textContent="Olá! 👋 Sua visita ao "+V.nome+" está marcada:\n📅 "+diaLab()+" às "+st.hora+"\n📍 "+$("vlocalinp").value+"\nAté lá! 😊";
    $("vresumo").innerHTML='Visita <b>'+diaLab()+' às '+st.hora+'</b> · '+V.quem+' vem ao <b>'+esc(V.nome)+'</b>';
  }
  document.addEventListener("click",function(e){
    var t=e.target.closest(".vchip");if(!t)return;
    if(t.dataset.d!=null)st.dia=t.dataset.d;
    else if(t.dataset.h!=null)st.hora=t.dataset.h;
    else if(t.dataset.dur!=null)st.dur=parseInt(t.dataset.dur,10);
    else if(t.dataset.lb!=null)st.lembr=parseInt(t.dataset.lb,10);
    else return;
    render();
  });
  $("vavisar").onclick=function(){var off=this.classList.toggle("off");this.classList.toggle("on",!off);this.textContent=off?"Desligado":"Ligado";prev();};
  $("vlocalinp").oninput=prev;
  $("vgo").onclick=function(){
    var b=$("vgo");b.disabled=true;b.textContent="Agendando…";
    fetch("/cockpit/lead/"+V.leadId+"/visita",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({data:st.dia,hora:st.hora,dur:st.dur,lembrete:st.lembr,local:$("vlocalinp").value,avisar:!$("vavisar").classList.contains("off")})})
      .then(function(r){return r.json();}).then(function(j){
        if(!j||!j.ok){toast((j&&j.erro)||"Não deu certo");b.disabled=false;b.textContent="📅 Agendar visita";return;}
        done(j);
      }).catch(function(){toast("Falha de conexão");b.disabled=false;b.textContent="📅 Agendar visita";});
  };
  function done(j){
    $("vbuild").style.display="none";$("vfoot").style.display="none";
    var aviso=j.avisado?'<div class=okline>✓ Confirmação + convite (.ics) enviados pro cliente</div>':'';
    var wa=j.zap?'<a class=wpp href="'+j.zap+'" target=_blank rel=noopener>💬 Enviar/reenviar pro cliente no WhatsApp</a>':'';
    $("vdone").innerHTML='<div class=orc-done><div class=big>📅✅</div><h3>Visita agendada!</h3>'
      +'<div class=evcard style="margin:1rem 0;border:1px solid #1e4a3a;background:#10241a;border-radius:13px;padding:1rem;text-align:left">'
      +'<div style="font-size:1.05rem;font-weight:700">📅 '+esc(j.quando)+'</div>'
      +'<div style="color:var(--verde-claro);font-size:.86rem;margin-top:.3rem">🏢 '+esc(j.empresa)+'</div>'
      +'<div style="color:var(--verde-claro);font-size:.86rem">📍 '+esc(j.local)+'</div>'
      +'<div style="color:var(--mut);font-size:.78rem;margin-top:.5rem">👥 Visitante: '+V.quem+' · 🎯 lead movido pra Qualificado</div></div>'
      +'<div class=okline>✓ Adicionado à agenda no Zaq</div>'+aviso
      +'<a class=ics href="'+esc(j.ics_url)+'" style="display:block;text-decoration:none;text-align:center" target=_blank rel=noopener>📎 Baixar convite .ics (calendário)</a>'
      +wa
      +'<a class=orc-ghost href="/cockpit/lead/'+V.leadId+'" style="display:block;border:0;color:var(--verde-claro);text-decoration:none">✓ Voltar pro lead</a>';
    $("vdone").style.display="block";
  }
  render();
})();
</script>"""


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
    # dono/gestor (inclusive logado no painel, sem membro_id) → Cockpit do Dono
    g = _gerencia(request)
    if g:
        return _dono_visao(request, g[0], g[1])
    sess = _sessao(request)
    if not sess:
        return RedirectResponse("/cockpit/login", status_code=303)
    conta_id, membro_id = sess
    pool = get_pool()
    leads = ck.leads_do_vendedor(pool, conta_id, membro_id)
    p = ck.perfil(pool, conta_id, membro_id)
    vez = sum(1 for l in leads if not l["ia"])
    from finance import empresa as emp
    marca = emp.marca_empresa(pool, conta_id)
    _blogo = (f"<img src='{esc(marca['logo_url'])}' alt='' style='width:26px;height:26px;border-radius:7px;object-fit:cover;background:#fff'>"
              if marca["logo_url"] else
              f"<span style='width:26px;height:26px;border-radius:7px;display:inline-flex;align-items:center;justify-content:center;font-weight:800;font-size:.66rem;color:#fff;background:{esc(marca['cor'])}'>{esc(marca['iniciais'])}</span>")
    brand = (f"<div style='display:flex;align-items:center;gap:.4rem;margin-left:auto'>{_blogo}"
             f"<span style='font-weight:600;font-size:.82rem;color:var(--txt)'>{esc(marca['nome'])}</span></div>")

    def _acts(ia: bool) -> str:
        main = ("<button class='act assumir' data-a=assumir><span class=em>🙋</span>Assumir</button>" if ia
                else "<button class='act devolver' data-a=devolver><span class=em>🤖</span>Devolver</button>")
        return f"<div class=actions>{main}<button class='act ganho' data-a=ganho><span class=em>✓</span>Ganho</button></div>"

    cards = []
    for l in leads:
        chip = ("<span class='cchip ia'>🤖 IA</span>" if l["ia"]
                else "<span class='cchip voce'>🙋 sua vez</span>")
        cards.append(
            "<div class=swipe>" + _acts(l["ia"]) +
            f"<div class='lead front' data-id='{l['id']}' data-ia='{1 if l['ia'] else 0}'>"
            f"<span class=dot style='background:{esc(l['temp_cor'])}'></span>"
            f"<span class=av>{esc(_ini(l['empresa']))}</span>"
            f"<span class=mid><span class=top><span class=emp>{esc(l['empresa'])}</span>{chip}</span>"
            f"<span class=snip>{esc(l['snip'])}</span></span>"
            "<span class=grab>⋮⋮</span></div></div>")
    if leads:
        lista = "".join(cards)
    else:
        lista = ("<div class=vazio><div class=big>🎯</div><b>Fila zerada!</b>"
                 "Nenhum lead aberto agora. Quando cair um novo no rodízio, você é avisado.</div>")

    from finance import webpush
    vapid = webpush.chave_publica()
    pushcard = ""
    if vapid:                        # só oferece push se as chaves VAPID estão no ambiente
        pushcard = ("<div class=pushcard id=pushcard><b>🔔 Ative as notificações</b>"
                    "<p>Receba um aviso no celular assim que um lead cair pra você — mesmo com o app fechado.</p>"
                    "<button class=go id=pushbtn type=button>Ativar notificações</button></div>")
    import json as _json
    inject = f"<script>window.CKVAPID={_json.dumps(vapid)};</script>" if vapid else ""

    body = (
        "<div class=hdr>"
        f"<a class=ib href='/cockpit/perfil' title='Meu perfil'>{esc(_ini(p['nome']))}</a>"
        f"<div class=tt><b>Meus leads</b><small>{len(leads)} abertos · {vez} sua vez</small></div>"
        f"{brand}</div>"
        "<div class=scroll>"
        + pushcard +
        "<div class=instpill>📲 <b>Instalar o app</b> — no menu do navegador, “Adicionar à tela inicial”. Deslize um card ← pra ações rápidas.</div>"
        f"{lista}</div>" + inject + _INBOX_JS)
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
        + f"<a class=fbtn href='/cockpit/lead/{lead_id}/ficha'>👤 Ficha</a>"
        + f"<a class='fbtn orc' href='/cockpit/lead/{lead_id}/orcamento' style='flex-basis:48%'>🧾 Orçamento</a>"
        + f"<a class='fbtn vis' href='/cockpit/lead/{lead_id}/visita' style='flex-basis:48%'>📅 Agendar visita</a>")
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
def _acao(request: Request, lead_id: int, fn):
    sess = _sessao(request)
    if not sess:
        if request.headers.get("x-cockpit") == "1":
            return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
        return RedirectResponse("/cockpit/login", status_code=303)
    conta_id, membro_id = sess
    r = fn(get_pool(), conta_id, membro_id, lead_id)
    # do swipe (fetch): responde JSON e fica na lista; senão redireciona pro lead
    if request.headers.get("x-cockpit") == "1":
        return JSONResponse(r)
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


@router.post("/cockpit/lead/{lead_id}/devolver")
def cockpit_devolver(request: Request, lead_id: int):
    return _acao(request, lead_id, lambda p, c, m, l: {**ck.devolver_ia(p, c, m, l),
                                                       "msg": "Devolvido pro agente ✓"})


@router.post("/cockpit/lead/{lead_id}/fechar")
def cockpit_fechar(request: Request, lead_id: int, tipo: str = Form(...), motivo: str = Form("")):
    sess = _sessao(request)
    if not sess:
        if request.headers.get("x-cockpit") == "1":
            return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
        return RedirectResponse("/cockpit/login", status_code=303)
    conta_id, membro_id = sess
    r = ck.fechar(get_pool(), conta_id, membro_id, lead_id, tipo, motivo)
    if request.headers.get("x-cockpit") == "1":     # swipe (fetch): fica na lista
        return JSONResponse(r)
    if r.get("ok"):
        request.session["ck_ok"] = "🎉 Marcado como Ganho!" if tipo == "ganho" else "Marcado como Perdido."
        return RedirectResponse("/cockpit", status_code=303)     # saiu da fila
    request.session["ck_err"] = r.get("erro", "Não deu certo.")
    return RedirectResponse(f"/cockpit/lead/{lead_id}", status_code=303)


# ------------------------------------------------------------------ push (assinar)
@router.get("/cockpit/push/chave")
def cockpit_push_chave(request: Request):
    from finance import webpush
    return JSONResponse({"chave": webpush.chave_publica()})


@router.post("/cockpit/push/assinar")
async def cockpit_push_assinar(request: Request, sub: dict = Body(...)):
    sess = _sessao(request)
    if not sess:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    ok = ck.salvar_assinatura(get_pool(), sess[0], sess[1], sub)
    return JSONResponse({"ok": bool(ok)})


@router.post("/cockpit/push/remover")
async def cockpit_push_remover(request: Request, sub: dict = Body(...)):
    if not _sessao(request):
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    ck.remover_assinatura(get_pool(), (sub or {}).get("endpoint", ""))
    return JSONResponse({"ok": True})


# ------------------------------------------------------------------ orçamento / proposta
@router.get("/cockpit/lead/{lead_id}/orcamento", response_class=HTMLResponse)
def cockpit_orcamento(request: Request, lead_id: int):
    sess = _sessao(request)
    if not sess:
        return RedirectResponse("/cockpit/login", status_code=303)
    conta_id, membro_id = sess
    pool = get_pool()
    d = ck.lead_do_vendedor(pool, conta_id, membro_id, lead_id)
    if not d:
        return RedirectResponse("/cockpit", status_code=303)
    cat = ck.catalogo_servicos(pool, conta_id)
    import json as _json
    cat_json = _json.dumps(cat, ensure_ascii=False).replace("</", "<\\/")   # acentos limpos; </script> seguro
    inject = f"<script>window.ORC={{cat:{cat_json},leadId:{lead_id}}};</script>"
    body = (
        "<div class=hdr>"
        f"<a class=bk href='/cockpit/lead/{lead_id}'>‹</a>"
        f"<div class=tt><b>Orçamento</b><small>{esc(d['empresa'])}</small></div></div>"
        "<div class='ck-toast' id=cktoast></div>"
        "<div class=scroll id=orcbuild>"
        "<div class=lbl style='padding:.7rem 1rem 0'>Serviços do catálogo</div>"
        "<div id=orclist></div>"
        "<div class=lbl style='padding:.9rem 1rem 0'>Adicionar item avulso</div>"
        "<div class=orc-add><input class=n id=orccnome placeholder='Ex.: Visita técnica' autocomplete=off>"
        "<input class=v id=orccval inputmode=numeric placeholder='R$' autocomplete=off>"
        "<button id=orcaddbtn type=button>+</button></div></div>"
        "<div class=orc-foot id=orcfoot>"
        "<div class=orc-tot><span>Total</span><span id=orctotal>—</span></div>"
        "<button class=orc-gen id=orcgen type=button disabled>Gerar proposta e link</button></div>"
        "<div id=orcdone style='display:none'></div>"
        + inject + _ORC_JS)
    return _page(f"Cockpit — orçamento {d['empresa']}", body)


@router.post("/cockpit/lead/{lead_id}/orcamento")
async def cockpit_orcamento_criar(request: Request, lead_id: int, payload: dict = Body(...)):
    sess = _sessao(request)
    if not sess:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    r = ck.criar_orcamento(get_pool(), sess[0], sess[1], lead_id, (payload or {}).get("itens"))
    return JSONResponse(r)


@router.post("/cockpit/lead/{lead_id}/orcamento/enviar")
async def cockpit_orcamento_enviar(request: Request, lead_id: int, payload: dict = Body(...)):
    sess = _sessao(request)
    if not sess:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    link = (payload or {}).get("link", "")
    if not link:
        return JSONResponse({"ok": False, "erro": "sem link"})
    r = ck.enviar_proposta_conversa(get_pool(), sess[0], sess[1], lead_id, link)
    return JSONResponse(r)


# ------------------------------------------------------------------ agendar visita
_DIA_SEM = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]
_HORAS = ["08:00", "09:00", "10:00", "11:00", "13:00", "14:00", "15:00", "16:00", "17:00", "18:00", "19:00"]


@router.get("/cockpit/lead/{lead_id}/visita", response_class=HTMLResponse)
def cockpit_visita(request: Request, lead_id: int):
    sess = _sessao(request)
    if not sess:
        return RedirectResponse("/cockpit/login", status_code=303)
    conta_id, membro_id = sess
    pool = get_pool()
    d = ck.lead_do_vendedor(pool, conta_id, membro_id, lead_id)
    if not d:
        return RedirectResponse("/cockpit", status_code=303)
    esp = ck.endereco_empresa(pool, conta_id)
    from datetime import datetime, timedelta
    from finance import agenda as ag
    hoje = datetime.now(ag.BRT).date()
    dias = []
    for i in range(0, 14):
        dt = hoje + timedelta(days=i)
        lab = "Hoje" if i == 0 else "Amanhã" if i == 1 else f"{_DIA_SEM[dt.weekday()]} {dt.day}"
        dias.append({"iso": dt.isoformat(), "lab": lab})
    quem = esc(d.get("contato") or d.get("empresa") or "o cliente")
    numero = d.get("whatsapp") or d.get("telefone") or ""
    import json as _json
    vis = {"leadId": lead_id, "dias": dias, "horas": _HORAS,
           "nome": esp["nome"], "quem": quem}
    inject = f"<script>window.VISITA={_json.dumps(vis, ensure_ascii=False)};</script>"
    maps_a = (f"<a href='{esc(esp['maps'])}' target=_blank rel=noopener>📍 Abrir no Google Maps</a>"
              if esp["maps"] else "")
    end_txt = esc(esp["endereco"] or "— sem endereço no cadastro da empresa —")
    body = (
        "<div class=hdr>"
        f"<a class=bk href='/cockpit/lead/{lead_id}'>‹</a>"
        f"<div class=tt><b>Agendar visita</b><small>{esc(d['empresa'])}</small></div></div>"
        "<div class='ck-toast' id=cktoast></div>"
        "<div class=scroll id=vbuild>"
        "<div class=vsec><div class=lbl>Quem vem visitar</div>"
        f"<div class=vvisit>👥 <b>{quem}</b>" + (f" <small>{esc(numero)}</small>" if numero else "") + "</div></div>"
        "<div class=vsec><div class=lbl>Dia</div><div class=vchips id=vdays></div></div>"
        "<div class=vsec><div class=lbl>Horário</div><div class='vchips vtimes' id=vtimes></div></div>"
        "<div class=vsec><div class=lbl>Duração</div><div class=vchips id=vdurs></div></div>"
        "<div class=vsec><div class=lbl>Local — o seu espaço</div>"
        f"<div class=vlocal><div class=nome>🏢 {esc(esp['nome'])}</div><div class=end>📍 {end_txt}</div>{maps_a}"
        f"<input id=vlocalinp value='{esc(esp['endereco'] or esp['nome'])}' aria-label='Endereço da visita'></div>"
        "<div style='color:var(--mut);font-size:.74rem;margin-top:.4rem'>vem do cadastro da empresa — edite se a visita for em outra unidade</div></div>"
        "<div class=vsec><div class=lbl>Lembrete (pra você)</div><div class=vchips id=vlembr></div></div>"
        "<div class=vsec><div class=vrow><div>Avisar o cliente no WhatsApp<div class=sub>confirmação + convite .ics (o calendário dele lembra)</div></div>"
        "<div class='tgl on' id=vavisar style='cursor:pointer'>Ligado</div></div><div class=vmsg id=vmsg></div></div>"
        "</div>"
        "<div class=orc-foot id=vfoot><div class=orc-tot><span id=vresumo>—</span></div>"
        "<button class=orc-gen id=vgo type=button>📅 Agendar visita</button></div>"
        "<div id=vdone style='display:none'></div>"
        + inject + _VISITA_JS)
    return _page(f"Cockpit — visita {d['empresa']}", body)


@router.post("/cockpit/lead/{lead_id}/visita")
async def cockpit_visita_criar(request: Request, lead_id: int, payload: dict = Body(...)):
    sess = _sessao(request)
    if not sess:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    p = payload or {}
    r = ck.agendar_visita(get_pool(), sess[0], sess[1], lead_id,
                          data=str(p.get("data") or ""), hora=str(p.get("hora") or ""),
                          dur_min=int(p.get("dur") or 60),
                          local=str(p.get("local") or ""),
                          lembrete_min=(int(p["lembrete"]) if p.get("lembrete") else None),
                          avisar_cliente=bool(p.get("avisar", True)))
    return JSONResponse(r)


@router.get("/visita/{token}.ics", include_in_schema=False)
def visita_ics_publico(request: Request, token: str):
    ics = ck.visita_ics(get_pool(), token)
    if not ics:
        return Response("visita não encontrada", status_code=404)
    return Response(ics, media_type="text/calendar; charset=utf-8",
                   headers={"Content-Disposition": 'attachment; filename="visita.ics"'})


# ================================================================== COCKPIT DO DONO
def _nav_dono(active: str) -> str:
    def a(key, ic, lab, href):
        return f"<a class='{'on' if active == key else ''}' href='{href}'><span class=i>{ic}</span>{lab}</a>"
    return ("<div class=dnav>" + a("visao", "📊", "Visão", "/cockpit")
            + a("placar", "🏆", "Placar", "/cockpit/equipe/placar")
            + a("ativ", "⚡", "Atividade", "/cockpit/equipe/atividade") + "</div>")


def _dono_hdr(conta_id: int, sub: str) -> str:
    nome = ck.endereco_empresa(get_pool(), conta_id)["nome"]
    return ("<div class=hdr><div class=ib>" + esc((nome or 'E')[:1].upper()) + "</div>"
            f"<div class=tt><b>Equipe · {esc(nome)}</b><small>{esc(sub)}</small></div></div>")


def _dono_visao(request: Request, conta_id: int, membro_id: int) -> HTMLResponse:
    from finance import cockpit_dono as cd
    periodo = request.query_params.get("p", "semana")
    if periodo not in ("hoje", "semana", "mes"):
        periodo = "semana"
    v = cd.visao(get_pool(), conta_id, periodo)
    k = v["kpis"]
    conv = f"{k['conversao']}%" if k["conversao"] is not None else "—"

    def seg(key, lab):
        return f"<a class='{'on' if periodo == key else ''}' href='/cockpit?p={key}'>{lab}</a>"
    kpis = (
        "<div class=dkpis>"
        f"<div class='dkpi hl'><div class=v>{k['novos']}</div><div class=l>Leads novos</div><div class=d>no período</div></div>"
        f"<div class=dkpi><div class=v>{k['com_ia'] + k['com_vend']}</div><div class=l>Em atendimento</div><div class=d>{k['com_ia']} c/ IA · {k['com_vend']} c/ vendedor</div></div>"
        f"<div class=dkpi><div class=v>{esc(k['ganhos_rs'])}</div><div class=l>Ganhos</div><div class=d>{k['ganhos']} fechado(s)</div></div>"
        f"<div class=dkpi><div class=v>{conv}</div><div class=l>Conversão</div><div class=d>ganhos vs. perdidos</div></div></div>")
    funil = "".join(
        f"<div class=dfr><span class=nm>{esc(f['rotulo'])}</span><span class=bar><i style='width:{f['pct']}%'></i></span>"
        f"<span class=qt><b>{f['n']}</b> <small>{esc(f['valor'])}</small></span></div>" for f in v["funil"])
    a = v["atencao"]
    aten = (
        "<div class=daten>"
        f"<div class='dat hot'><div class=n>{a['parados']}</div><div class=t>leads parados +3 dias</div></div>"
        f"<div class='dat warn'><div class=n>{a['quentes']}</div><div class=t>quentes sem contato hoje</div></div>"
        f"<div class='dat info'><div class=n>{a['propostas']}</div><div class=t>propostas aguardando</div></div>"
        f"<div class='dat info'><div class=n>{a['visitas']}</div><div class=t>visitas hoje</div></div></div>")
    body = (
        _dono_hdr(conta_id, "acompanhe o time")
        + f"<div class=dseg>{seg('hoje','Hoje')}{seg('semana','Semana')}{seg('mes','Mês')}</div>"
        + "<div class=scroll>" + kpis
        + "<div class=dsect>Funil do time <span>leads · valor</span></div>" + f"<div class=dfunil>{funil}</div>"
        + "<div class=dsect>Precisa de atenção</div>" + aten + "</div>"
        + _nav_dono("visao"))
    return _page("Cockpit do Dono", body)


@router.get("/cockpit/equipe/placar", response_class=HTMLResponse)
def cockpit_dono_placar(request: Request):
    g = _gerencia(request)
    if not g:
        return RedirectResponse("/cockpit", status_code=303)
    from finance import cockpit_dono as cd
    lista = cd.placar(get_pool(), g[0])
    medalhas = ["🥇", "🥈", "🥉"]
    linhas = []
    for i, v in enumerate(lista):
        rk = medalhas[i] if i < 3 else str(i + 1)
        paus = " <span class=dpaus>pausado</span>" if v["pausado"] else ""
        linhas.append(
            f"<a class=dvend href='/cockpit/equipe/vendedor/{v['id']}'>"
            f"<span class=rk>{rk}</span><span class=av>{esc(v['nome'][:1].upper())}</span>"
            f"<div class=mid><b>{esc(v['nome'])}</b><div class=sub><span>📥 {v['fila']} fila</span>"
            f"<span>💬 {v['atendendo']} atend.</span><span>⚡ {esc(v['resp'])}</span>{paus}</div></div>"
            f"<div class=rt><span class=g>{esc(v['rs'])}</span><small>{v['ganhos']} ganhos · {esc(v['conversao'])}</small></div></a>")
    corpo = "".join(linhas) or "<div class=dvline style='padding:2rem'>Nenhum vendedor na equipe ainda.</div>"
    body = (_dono_hdr(g[0], "placar · este mês, por R$ fechado")
            + f"<div class=scroll>{corpo}</div>" + _nav_dono("placar"))
    return _page("Cockpit do Dono — placar", body)


@router.get("/cockpit/equipe/atividade", response_class=HTMLResponse)
def cockpit_dono_atividade(request: Request):
    g = _gerencia(request)
    if not g:
        return RedirectResponse("/cockpit", status_code=303)
    from finance import cockpit_dono as cd
    feed = cd.atividade(get_pool(), g[0])
    ic = {"ganho": "🎉", "perdido": "✕", "visita": "📅", "prop": "🧾"}
    linhas = "".join(
        f"<div class='dev {e['tipo']}'><span class=ic>{ic.get(e['tipo'], '•')}</span><div class=tx>{esc(e['txt'])}</div></div>"
        for e in feed) or "<div class=dvline style='padding:2rem'>Sem atividade recente.</div>"
    body = (_dono_hdr(g[0], "atividade do time")
            + f"<div class=scroll>{linhas}</div>" + _nav_dono("ativ"))
    return _page("Cockpit do Dono — atividade", body)


@router.get("/cockpit/equipe/vendedor/{membro_id}", response_class=HTMLResponse)
def cockpit_dono_vendedor(request: Request, membro_id: int):
    g = _gerencia(request)
    if not g:
        return RedirectResponse("/cockpit", status_code=303)
    from finance import cockpit_dono as cd
    v = cd.vendedor(get_pool(), g[0], membro_id)
    if not v:
        return RedirectResponse("/cockpit/equipe/placar", status_code=303)
    outros = cd.vendedores_para_reatribuir(get_pool(), g[0], membro_id)
    opts = "".join(f"<option value='{o['id']}'>{esc(o['nome'])}</option>" for o in outros)
    leads = ""
    for l in v["leads"]:
        chip = "🤖 IA" if l["ia"] else "🙋 vend."
        reat = ("" if not outros else
                f"<form method=post action='/cockpit/equipe/reatribuir'>"
                f"<input type=hidden name=lead_id value='{l['id']}'>"
                f"<select name=para><option value=''>mover p/…</option>{opts}</select>"
                f"<button type=submit>↦</button></form>")
        leads += (f"<div class=dlead><span class=dot2 style='background:{esc(l['temp_cor'])}'></span>"
                  f"<div class=m><b>{esc(l['empresa'])}</b></div><span class='cchip {'ia' if l['ia'] else 'voce'}'>{chip}</span>{reat}</div>")
    if not v["leads"]:
        leads = "<div class=dvline style='padding:1.5rem'>Sem leads abertos." + (" Está pausado." if v["pausado"] else "") + "</div>"
    pause_lbl = "▶ Reativar no rodízio" if v["pausado"] else "⏸ Pausar no rodízio"
    body = (
        "<div class=hdr><a class=bk href='/cockpit/equipe/placar'>‹</a>"
        f"<div class=tt><b>{esc(v['nome'])}</b><small>vendedor · {'pausado no rodízio' if v['pausado'] else 'no rodízio'}</small></div></div>"
        + _flash(request)
        + "<div class=scroll>"
        + "<div class=dvstat>"
        + f"<div class=s><b>{v['fila']}</b><small>na fila</small></div>"
        + f"<div class=s><b>{v['ganhos']}</b><small>ganhos/mês</small></div>"
        + f"<div class=s><b>{esc(v['conversao'])}</b><small>conversão</small></div></div>"
        + f"<div class=dvline><span>💰 <b>{esc(v['rs'])}</b> fechado</span><span>⏱️ <b>{esc(v['resp'])}</b> resposta</span></div>"
        + "<div class=dvact><form method=post action='/cockpit/equipe/pausar'>"
        + f"<input type=hidden name=membro_id value='{membro_id}'><input type=hidden name=on value='{0 if v['pausado'] else 1}'>"
        + f"<button class=pausebtn type=submit>{pause_lbl}</button></form></div>"
        + f"<div class=dsect>Leads dele <span>{len(v['leads'])} abertos</span></div>{leads}</div>"
        + _nav_dono("placar"))
    return _page(f"Cockpit do Dono — {v['nome']}", body)


@router.post("/cockpit/equipe/reatribuir")
def cockpit_dono_reatribuir(request: Request, lead_id: int = Form(...), para: str = Form("")):
    g = _gerencia(request)
    if not g:
        return RedirectResponse("/cockpit", status_code=303)
    origem = "/cockpit/equipe/placar"
    if para.strip().isdigit():
        r = cd_reatribuir(g[0], lead_id, int(para))
        request.session["ck_ok" if r.get("ok") else "ck_err"] = (
            "Lead reatribuído ✓" if r.get("ok") else "Não consegui reatribuir.")
        origem = f"/cockpit/equipe/vendedor/{int(para)}"
    return RedirectResponse(origem, status_code=303)


@router.post("/cockpit/equipe/pausar")
def cockpit_dono_pausar(request: Request, membro_id: int = Form(...), on: str = Form("1")):
    g = _gerencia(request)
    if not g:
        return RedirectResponse("/cockpit", status_code=303)
    from finance import cockpit_dono as cd
    cd.pausar(get_pool(), g[0], membro_id, on == "1")
    request.session["ck_ok"] = "Rodízio pausado ✓" if on == "1" else "Vendedor reativado ✓"
    return RedirectResponse(f"/cockpit/equipe/vendedor/{membro_id}", status_code=303)


def cd_reatribuir(conta_id, lead_id, para):
    from finance import cockpit_dono as cd
    return cd.reatribuir(get_pool(), conta_id, lead_id, para)


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
