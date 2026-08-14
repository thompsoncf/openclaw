"""Cockpit NOVO — protótipo do app do vendedor e do gestor na marca atual do Zaq.

Por que existe um segundo arquivo: o `/cockpit` que está no ar não pode parar
enquanto a gente redesenha. Então isto aqui é **aditivo** — mora sob `/cockpit/novo`,
não substitui nada, e o app antigo continua intacto até esta versão ser aprovada.

O que muda em relação ao `/cockpit`:

  * **Marca.** O app antigo usa a paleta legada do hortifruti (#0e0e0f / #1d9e75,
    fonte de sistema); o site (zaq-ia.com) já usa outra — fundo #0A0F0C, neon
    #25D366, Bricolage Grotesque + Inter + JetBrains Mono. Os tokens abaixo são os
    do site, copiados de zaq-landing/index.html, pra quem sai do site e entra no
    app não achar que trocou de produto. Ícones viram SVG (o antigo usa emoji).
  * **O vendedor ganha um app.** Antes era UMA tela (a fila) sem menu nenhum:
    perfil, visitas e resultado ficavam escondidos atrás do avatar. Agora são
    quatro abas — Fila, Agenda, Resultado, Perfil.
  * **O vendedor vê dinheiro.** `membros.comissao_pct` existe desde a migração 137
    mas só aparecia pro dono, no relatório do painel. A aba Resultado mostra o que
    ELE fechou e quanto disso é comissão dele.
  * **O gestor não é mais expulso.** No app antigo, tocar num lead da equipe
    levava pra /painel/prospeccao/{id} — o painel desktop, no meio do celular.
    Aqui o lead abre dentro do próprio app.

Fronteira mantida: aqui só HTTP + HTML. O motor (posse, escopo, escrita) continua
em finance/cockpit.py e finance/cockpit_dono.py. **Nenhuma escrita nova**: os forms
postam nos endpoints que já existem em web/painel_cockpit.py e voltam pra cá, então
o protótipo não tem caminho próprio de gravar — não tem como corromper dado.

Guards: `_sessao`/`_gerencia` são importados de web/painel_cockpit.py em vez de
reescritos. Isso importa porque o middleware central (web/app.py) só filtra
/painel* e /membros* — /cockpit* passa livre e a checagem é toda daqui.
"""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from db.conexao import get_pool
from finance import cockpit as ck
from finance import cockpit_dono as cd
from web.painel_cockpit import esc, _sessao, _gerencia, _ini

router = APIRouter()

_BASE = "/cockpit/novo"


# ================================================================== marca
# Tokens copiados de zaq-landing/index.html:17-24 (a marca do site). Os semânticos
# (--ambar/--coral/--azul/--roxo) o site não define — o app precisa deles pra
# temperatura de lead e estados de atenção, então ficam no vocabulário do app.
_CSS = """
<link rel=preconnect href="https://fonts.googleapis.com">
<link rel=preconnect href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,600;12..96,700;12..96,800&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@500;700&display=swap" rel=stylesheet>
<style>
:root{
  color-scheme:dark;
  --bg:#0A0F0C; --bg-2:#0E1512; --surface:#121A16; --line:#1E2A23;
  --neon:#25D366; --neon-bright:#46F58A; --neon-deep:#0FA85A;
  --text:#EAF2ED; --text-dim:#8FA197; --text-faint:#5E6F66;
  --ink:#04150C;                 /* texto sobre o verde — o site diverge (#04150C x
                                    #07110B); aqui fica um só, e este é o canônico */
  --ambar:#E0A32E; --coral:#E0574F; --azul:#229ED9; --roxo:#C9A3E0;
  --display:"Bricolage Grotesque",system-ui,sans-serif;
  --body:"Inter",system-ui,sans-serif;
  --mono:"JetBrains Mono",ui-monospace,monospace;
}
*{box-sizing:border-box}html,body{margin:0}
body{background:var(--bg);color:var(--text);font-family:var(--body);line-height:1.5;
  -webkit-font-smoothing:antialiased;overflow:hidden}
a{color:inherit;text-decoration:none}
b,strong{font-weight:600}
/* app shell: a página não rola; só o miolo (.scroll) rola, então header e abas
   ficam parados como em app nativo. */
.wrap{max-width:520px;margin:0 auto;height:100vh;height:100dvh;overflow:hidden;
  display:flex;flex-direction:column;position:relative}
/* o glow é a assinatura visual do site — aqui entra atrás do topo, discreto */
.glow{position:absolute;top:-160px;left:50%;transform:translateX(-50%);width:150%;height:320px;
  pointer-events:none;z-index:0;background:radial-gradient(ellipse at center,rgba(37,211,102,.16),transparent 60%);
  filter:blur(20px)}
.scroll{flex:1;overflow-y:auto;position:relative;z-index:1;padding-bottom:1.2rem}
.scroll::-webkit-scrollbar{width:0}

/* ---------- header ---------- */
.hdr{display:flex;align-items:center;gap:.7rem;padding:.85rem 1.1rem;flex-shrink:0;
  border-bottom:1px solid var(--line);background:rgba(10,15,12,.72);backdrop-filter:blur(12px);
  position:relative;z-index:2}
.hdr .tt{flex:1;min-width:0}
.hdr .tt b{font-family:var(--display);font-weight:700;font-size:1.05rem;letter-spacing:-.02em;
  display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.hdr .tt small{color:var(--text-dim);font-size:.76rem;display:block}
.hdr .bk{display:grid;place-items:center;width:34px;height:34px;margin-left:-.5rem;flex-shrink:0;
  border-radius:10px;color:var(--text)}
.hdr .bk:active{background:var(--surface)}
.av{width:36px;height:36px;border-radius:50%;flex-shrink:0;display:grid;place-items:center;
  font-family:var(--display);font-weight:800;font-size:.9rem;
  background:linear-gradient(145deg,var(--neon),var(--neon-deep));color:var(--ink);
  box-shadow:0 0 16px rgba(37,211,102,.35)}
.av.mudo{background:var(--surface);color:var(--text-dim);border:1px solid var(--line);box-shadow:none}

/* ---------- tipografia de apoio ---------- */
.eyebrow{font-family:var(--mono);font-size:.68rem;letter-spacing:.18em;text-transform:uppercase;
  color:var(--neon);display:inline-flex;align-items:center;gap:.5rem;margin:1.1rem 1.1rem .5rem}
.eyebrow::before{content:"";width:6px;height:6px;border-radius:50%;background:var(--neon);
  box-shadow:0 0 9px var(--neon);animation:pul 2s infinite}
@keyframes pul{0%,100%{opacity:1}50%{opacity:.3}}
.num{font-family:var(--mono);font-weight:700;letter-spacing:-.02em}
.mut{color:var(--text-dim)}
.vazio{text-align:center;color:var(--text-dim);padding:3rem 1.6rem;font-size:.9rem}
.vazio .big{font-size:2.4rem;margin-bottom:.5rem;opacity:.5}
.vazio b{display:block;color:var(--text);font-family:var(--display);font-size:1.1rem;margin-bottom:.3rem}

/* ---------- blocos ---------- */
.bloco{margin:0 1.1rem .8rem}
.card{background:var(--surface);border:1px solid var(--line);border-radius:16px;padding:.9rem 1rem}
.kpis{display:grid;grid-template-columns:1fr 1fr;gap:.6rem;margin:0 1.1rem .9rem}
.kpi{background:var(--surface);border:1px solid var(--line);border-radius:16px;padding:.85rem .9rem}
.kpi .v{font-family:var(--mono);font-weight:700;font-size:1.5rem;letter-spacing:-.03em;line-height:1.1}
.kpi .l{font-size:.8rem;margin-top:.15rem}
.kpi .d{font-size:.7rem;color:var(--text-faint);margin-top:.1rem}
.kpi.hero{grid-column:1/-1;background:linear-gradient(150deg,#0E2018,#0b1612);border-color:#20342a}
.kpi.hero .v{font-size:2.1rem;color:var(--neon)}

/* ---------- seg (Hoje/Semana/Mês) ---------- */
.seg{display:flex;gap:3px;margin:.9rem 1.1rem;padding:3px;background:var(--surface);
  border:1px solid var(--line);border-radius:999px}
.seg a{flex:1;text-align:center;padding:.42rem;border-radius:999px;font-size:.82rem;color:var(--text-dim)}
.seg a.on{background:var(--neon);color:var(--ink);font-weight:600}

/* ---------- lista de leads ---------- */
.lead{display:flex;align-items:center;gap:.75rem;padding:.8rem 1.1rem;border-bottom:1px solid var(--line)}
.lead:active{background:var(--bg-2)}
.lead .dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.lead .mid{flex:1;min-width:0}
.lead .top{display:flex;align-items:center;gap:.45rem}
.lead .emp{font-weight:600;font-size:.94rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.lead .snip{display:block;color:var(--text-dim);font-size:.78rem;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap;margin-top:.05rem}
.chip{font-size:.66rem;padding:.14rem .5rem;border-radius:999px;border:1px solid var(--line);
  color:var(--text-dim);flex-shrink:0;white-space:nowrap}
.chip.ia{color:var(--roxo);border-color:#3a2b52;background:#1a1226}
.chip.voce{color:var(--ambar);border-color:#5a4520;background:#241c0f}
.chip.neon{color:var(--neon);border-color:#1e4a3a;background:rgba(37,211,102,.10)}
.chip.err{color:var(--coral);border-color:#5a2b2b;background:#241313}

/* ---------- link da proposta pra copiar ---------- */
.copiar{display:flex;gap:.4rem;margin-top:.5rem}
.copiar input{flex:1;min-width:0;background:var(--bg-2);border:1px solid var(--line);
  border-radius:10px;color:var(--neon);padding:.55rem .7rem;font-family:var(--mono);font-size:.72rem}
.copiar button{flex-shrink:0;background:var(--surface);border:1px solid var(--line);
  border-radius:10px;color:var(--text);padding:0 .9rem;font:inherit;font-weight:600;cursor:pointer}

/* ---------- funil ---------- */
.fr{display:flex;align-items:center;gap:.6rem;padding:.42rem 0}
.fr .nm{width:88px;flex-shrink:0;font-size:.82rem;color:var(--text-dim)}
.fr .bar{flex:1;height:14px;border-radius:7px;background:var(--bg-2);border:1px solid var(--line);overflow:hidden}
.fr .bar i{display:block;height:100%;background:linear-gradient(90deg,var(--neon-deep),var(--neon))}
.fr .qt{width:74px;text-align:right;flex-shrink:0;font-size:.78rem}
.fr .qt b{font-family:var(--mono)}
.fr .qt small{display:block;color:var(--text-faint);font-size:.68rem}

/* ---------- atenção ---------- */
.aten{display:grid;grid-template-columns:1fr 1fr;gap:.6rem;margin:0 1.1rem .9rem}
.at{border:1px solid var(--line);background:var(--surface);border-radius:14px;padding:.7rem .8rem}
.at .n{font-family:var(--mono);font-weight:700;font-size:1.3rem;line-height:1.1}
.at .t{font-size:.74rem;color:var(--text-dim);margin-top:.1rem}
.at.hot{border-color:#5a2b2b;background:#241313}.at.hot .n{color:var(--coral)}
.at.warn{border-color:#5a4520;background:#241c0f}.at.warn .n{color:var(--ambar)}
.at.info{border-color:#1b3a4a;background:#0d1b23}.at.info .n{color:var(--azul)}

/* ---------- pódio + placar ---------- */
/* pódio: os itens ESTICAM na altura da linha e empurram o conteúdo pro fim
   (justify-content:flex-end), então as bases terminam todas na mesma linha —
   com align-items:end cada item media a própria altura e elas desalinhavam. */
.podio{display:grid;grid-template-columns:1fr 1.15fr 1fr;align-items:stretch;gap:.5rem;
  margin:1.1rem 1.1rem 0;border-bottom:1px solid var(--line)}
.pod{display:flex;flex-direction:column;justify-content:flex-end;align-items:center;
  text-align:center;min-width:0;width:100%}
.pod .av{margin-bottom:.4rem;box-shadow:none}
.pod .nm{font-size:.78rem;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pod .rs{font-family:var(--mono);font-weight:700;font-size:.82rem;color:var(--neon)}
.pod .base{width:100%;margin-top:.5rem;border-radius:10px 10px 0 0;background:var(--surface);
  border:1px solid var(--line);border-bottom:0;display:grid;place-items:center;
  font-family:var(--display);font-weight:800;font-size:.9rem;color:var(--text-faint)}
.pod.p1 .base{height:62px;background:linear-gradient(180deg,rgba(37,211,102,.18),transparent);
  border-color:#1e4a3a;color:var(--neon)}
.pod.p1 .av{width:46px;height:46px;font-size:1.05rem;box-shadow:0 0 18px rgba(37,211,102,.4)}
.pod.p2 .base{height:44px}.pod.p3 .base{height:32px}
.linha{display:flex;align-items:center;gap:.7rem;padding:.75rem 1.1rem;border-bottom:1px solid var(--line)}
.linha:active{background:var(--bg-2)}
.linha .rk{width:20px;flex-shrink:0;text-align:center;font-family:var(--mono);color:var(--text-faint);font-size:.8rem}
.linha .mid{flex:1;min-width:0}
.linha .mid b{font-size:.9rem}
.linha .sub{display:flex;gap:.6rem;flex-wrap:wrap;color:var(--text-faint);font-size:.7rem;margin-top:.1rem}
.linha .rt{text-align:right;flex-shrink:0}
.linha .rt .g{font-family:var(--mono);font-weight:700;color:var(--neon);font-size:.9rem}
.linha .rt small{display:block;color:var(--text-faint);font-size:.66rem}
.pausado{color:var(--ambar);border:1px solid #5a4520;background:#241c0f;border-radius:999px;
  padding:0 .35rem;font-size:.62rem}

/* ---------- feed ---------- */
.ev{display:flex;gap:.7rem;align-items:flex-start;padding:.7rem 1.1rem;border-bottom:1px solid var(--line)}
.ev .ic{width:30px;height:30px;border-radius:9px;flex-shrink:0;display:grid;place-items:center;
  background:var(--surface);border:1px solid var(--line);color:var(--text-dim)}
.ev.ganho .ic{background:rgba(37,211,102,.12);border-color:#1e4a3a;color:var(--neon)}
.ev.perdido .ic{background:#241313;border-color:#5a2b2b;color:var(--coral)}
.ev.visita .ic{background:#0d1b23;border-color:#1b3a4a;color:var(--azul)}
.ev.prop .ic{background:#1a1226;border-color:#3a2b52;color:var(--roxo)}
.ev .tx{flex:1;font-size:.85rem;padding-top:.15rem}

/* ---------- filtros ---------- */
.filt{display:flex;gap:.35rem;align-items:center;overflow-x:auto;padding:.45rem 1.1rem;
  scrollbar-width:none}
.filt::-webkit-scrollbar{height:0}
.filt .lbl{font-family:var(--mono);font-size:.62rem;letter-spacing:.12em;text-transform:uppercase;
  color:var(--text-faint);flex-shrink:0;margin-right:.15rem}
.filt a{flex-shrink:0;font-size:.76rem;padding:.28rem .65rem;border-radius:999px;
  border:1px solid var(--line);color:var(--text-dim);background:var(--surface)}
.filt a.on{border-color:var(--neon);background:rgba(37,211,102,.12);color:var(--neon);font-weight:600}

/* ---------- agenda ---------- */
.vis{display:flex;gap:.8rem;padding:.85rem 1.1rem;border-bottom:1px solid var(--line);align-items:flex-start}
.vis .quando{width:56px;flex-shrink:0;text-align:center}
.vis .quando .h{font-family:var(--mono);font-weight:700;font-size:1rem}
.vis .quando .d{font-size:.66rem;color:var(--text-faint)}
.vis.hoje .quando .h{color:var(--neon)}
.vis .mid{flex:1;min-width:0}
.vis .mid b{font-size:.9rem;display:block}
.vis .mid .loc{font-size:.76rem;color:var(--text-dim);margin-top:.1rem}
.acoes{display:flex;gap:.4rem;flex-wrap:wrap;margin-top:.5rem}
.acoes a{font-size:.74rem;padding:.3rem .65rem;border-radius:9px;border:1px solid var(--line);
  background:var(--bg-2);color:var(--text-dim)}
.acoes a:active{border-color:var(--neon)}

/* ---------- botões ---------- */
.btn{display:flex;align-items:center;justify-content:center;gap:.5rem;width:100%;
  background:var(--neon);color:var(--ink);font-family:var(--display);font-weight:700;
  font-size:.95rem;padding:.8rem;border:0;border-radius:13px;cursor:pointer;
  box-shadow:0 8px 30px rgba(37,211,102,.32)}
.btn:active{transform:translateY(1px)}
.btn.ghost{background:transparent;color:var(--text-dim);border:1px solid var(--line);box-shadow:none;font-weight:600}
.btn.perigo{background:transparent;color:var(--coral);border:1px solid #5a2b2b;box-shadow:none}
.linhaform{display:flex;gap:.45rem;align-items:center}
select{flex:1;min-width:0;background:var(--bg-2);border:1px solid var(--line);border-radius:10px;
  color:var(--text);padding:.55rem .6rem;font-family:inherit;font-size:.85rem}

/* ---------- chat (tela do lead) ---------- */
.chat{flex:1;overflow-y:auto;padding:.9rem 1.1rem;display:flex;flex-direction:column;gap:.5rem;
  background:#0B141A;position:relative;z-index:1}
.bub{max-width:82%;padding:.5rem .75rem;border-radius:13px;font-size:.87rem;white-space:pre-wrap;
  word-break:break-word}
.bub.in{align-self:flex-start;background:#1F2C25;border-bottom-left-radius:4px}
.bub.out{align-self:flex-end;background:#0A5C49;border-bottom-right-radius:4px}
.bub.ia{align-self:flex-end;background:#1a1226;border:1px solid #3a2b52;border-bottom-right-radius:4px}
.bub .who{font-size:.64rem;color:var(--text-faint);margin-bottom:.15rem}
.aviso{margin:.2rem 0;padding:.55rem .7rem;border-radius:11px;font-size:.78rem;text-align:center;
  background:var(--surface);border:1px solid var(--line);color:var(--text-dim)}
.rodape{flex-shrink:0;border-top:1px solid var(--line);background:var(--bg);padding:.7rem 1.1rem;
  padding-bottom:calc(.7rem + env(safe-area-inset-bottom,0px));position:relative;z-index:2}
.composer{display:flex;gap:.5rem;align-items:center}
.composer input{flex:1;min-width:0;background:var(--surface);border:1px solid var(--line);
  border-radius:999px;color:var(--text);padding:.65rem .95rem;font-family:inherit;font-size:.9rem}
.composer input:focus{outline:none;border-color:var(--neon)}
.composer button{width:42px;height:42px;flex-shrink:0;border:0;border-radius:50%;cursor:pointer;
  background:var(--neon);color:var(--ink);font-size:1.1rem;display:grid;place-items:center}
/* ficha/funil viram uma folha que sobe — no app antigo isso empilhava ACIMA do chat
   e empurrava a conversa (a superfície de trabalho real) pra fora da tela */
.folha{position:absolute;left:0;right:0;bottom:0;z-index:20;background:var(--bg-2);
  border-top:1px solid var(--line);border-radius:18px 18px 0 0;padding:.5rem 1.1rem 1.2rem;
  padding-bottom:calc(1.2rem + env(safe-area-inset-bottom,0px));
  max-height:84%;overflow-y:auto;overscroll-behavior:contain;
  transform:translateY(101%);transition:transform .22s ease}
.folha:target{transform:translateY(0)}
.folha .puxa{width:36px;height:4px;border-radius:2px;background:var(--line);margin:.2rem auto .8rem;
  position:sticky;top:0}
.folha h3{font-family:var(--display);font-size:.95rem;margin:.6rem 0 .5rem;font-weight:700}
/* o fundo escuro vem DEPOIS da folha no HTML: `~` só enxerga irmão posterior */
.fbg{position:absolute;inset:0;z-index:19;background:rgba(0,0,0,.55);display:none}
.folha:target ~ .fbg{display:block}
.grade{display:grid;grid-template-columns:1fr 1fr;gap:.45rem}
.grade a,.grade .off{display:flex;align-items:center;justify-content:center;gap:.4rem;
  padding:.6rem .4rem;border-radius:11px;border:1px solid var(--line);background:var(--surface);
  font-size:.82rem;color:var(--text)}
.grade .off{opacity:.35}
.grade a.orc{border-color:#1e4a3a;color:var(--neon);background:rgba(37,211,102,.08);font-weight:600}
.grade a.vis2{border-color:#1b3a4a;color:var(--azul);background:#0d1b23;font-weight:600}
.etapas{display:flex;gap:.35rem;flex-wrap:wrap}
.etapas form{margin:0}
.etapas button{font-family:inherit;font-size:.78rem;padding:.32rem .7rem;border-radius:999px;
  border:1px solid var(--line);background:var(--surface);color:var(--text-dim);cursor:pointer}
.etapas button.on{border-color:var(--neon);background:rgba(37,211,102,.12);color:var(--neon);font-weight:600}
.ficha-l{display:flex;justify-content:space-between;gap:1rem;padding:.4rem 0;
  border-bottom:1px solid var(--line);font-size:.84rem}
.ficha-l span{color:var(--text-dim);flex-shrink:0}
.ficha-l b{text-align:right;font-weight:500;word-break:break-word}

/* ---------- abas de baixo ---------- */
.tabs{display:flex;flex-shrink:0;border-top:1px solid var(--line);background:rgba(10,15,12,.92);
  backdrop-filter:blur(12px);padding-bottom:env(safe-area-inset-bottom,0px);position:relative;z-index:2}
.tabs a{flex:1;display:flex;flex-direction:column;align-items:center;gap:2px;padding:.5rem 0 .45rem;
  color:var(--text-faint);font-size:.62rem}
.tabs a.on{color:var(--neon)}
.tabs a.on .ic{filter:drop-shadow(0 0 6px rgba(37,211,102,.5))}
.ic{width:21px;height:21px;stroke:currentColor;fill:none;stroke-width:1.7;
  stroke-linecap:round;stroke-linejoin:round}
.ic.p{width:17px;height:17px}

/* ---------- recado da última ação ---------- */
.flash{margin:.7rem 1.1rem 0;padding:.6rem .8rem;border-radius:12px;font-size:.84rem}
.flash.ok{background:rgba(37,211,102,.12);border:1px solid #1e4a3a;color:var(--neon)}
.flash.err{background:#241313;border:1px solid #5a2b2b;color:var(--coral)}

/* ---------- faixa de protótipo ---------- */
.previa{display:flex;align-items:center;gap:.4rem;justify-content:center;padding:.35rem;
  font-family:var(--mono);font-size:.62rem;letter-spacing:.1em;text-transform:uppercase;
  background:rgba(37,211,102,.10);border-bottom:1px solid #1e4a3a;color:var(--neon);flex-shrink:0}
.previa a{color:var(--text-dim);text-decoration:underline;text-transform:none;letter-spacing:0}
.fonte{margin:.2rem 1.1rem 1rem;font-size:.7rem;color:var(--text-faint);line-height:1.45}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
</style>"""

# Ícones no traço do set que o painel já usa (web/portal.py) — stroke 1.7, viewBox 24.
# O app antigo usava emoji no menu (📊 🏆 📋 ⚡), que muda de desenho a cada sistema.
# NB: todo atributo vai ENTRE ASPAS. Sem aspas, o parser de HTML engole a barra do
# fecho no valor (r=3.6/> vira r="3.6/") e o desenho some — foi o que aconteceu com
# o ícone de perfil na primeira tentativa.
_ICONES = (
    '<svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs>'
    '<symbol id="i-fila" viewBox="0 0 24 24"><path d="M4 5h16l1.5 8v6h-19v-6z"/>'
    '<path d="M2.5 13H8l1.4 2.6h5.2L16 13h5.5"/></symbol>'
    '<symbol id="i-agenda" viewBox="0 0 24 24"><rect x="3.5" y="5.5" width="17" height="15" rx="2"/>'
    '<path d="M3.5 10h17M8 3v5M16 3v5"/></symbol>'
    '<symbol id="i-resultado" viewBox="0 0 24 24"><path d="M3 21h18"/>'
    '<path d="M6.5 21v-5M11 21V9M15.5 21v-8M20 21V5"/></symbol>'
    '<symbol id="i-perfil" viewBox="0 0 24 24"><circle cx="12" cy="8" r="3.6"/>'
    '<path d="M4.6 20c0-3.9 3.3-6.2 7.4-6.2s7.4 2.3 7.4 6.2"/></symbol>'
    '<symbol id="i-visao" viewBox="0 0 24 24">'
    '<path d="M4 4h7v7H4zM13 4h7v4h-7zM13 11h7v9h-7zM4 14h7v6H4z"/></symbol>'
    '<symbol id="i-placar" viewBox="0 0 24 24"><path d="M8 3.5h8V9a4 4 0 01-8 0z"/>'
    '<path d="M8 5H5v1.5A3.5 3.5 0 008.5 10M16 5h3v1.5A3.5 3.5 0 0115.5 10"/>'
    '<path d="M12 13v3.5M9 20.5h6M10.5 16.5h3"/></symbol>'
    '<symbol id="i-leads" viewBox="0 0 24 24"><path d="M9 6h11M9 12h11M9 18h11"/>'
    '<circle cx="4.5" cy="6" r="1.3"/><circle cx="4.5" cy="12" r="1.3"/>'
    '<circle cx="4.5" cy="18" r="1.3"/></symbol>'
    '<symbol id="i-ativ" viewBox="0 0 24 24"><path d="M13 2.5L5.5 13.5H11L10 21.5L18.5 10.5H13z"/></symbol>'
    '<symbol id="i-volta" viewBox="0 0 24 24"><path d="M15 5l-7 7 7 7"/></symbol>'
    '<symbol id="i-ligar" viewBox="0 0 24 24"><path d="M6.5 3.5h3l1.5 4-2 1.5a12 12 0 006 6l1.5-2 4 1.5v3'
    'a2 2 0 01-2.2 2A17 17 0 014.5 5.7 2 2 0 016.5 3.5z"/></symbol>'
    '<symbol id="i-zap" viewBox="0 0 24 24">'
    '<path d="M3.5 20.5l1.3-4.4A8.2 8.2 0 1112 20.3a8.4 8.4 0 01-4.1-1.1z"/></symbol>'
    '<symbol id="i-ficha" viewBox="0 0 24 24"><rect x="4" y="3" width="16" height="18" rx="2"/>'
    '<path d="M8 8h8M8 12h8M8 16h5"/></symbol>'
    '<symbol id="i-orc" viewBox="0 0 24 24"><path d="M6 3h12v18l-2.5-1.6L13 21l-2.5-1.6L8 21l-2-1.6z"/>'
    '<path d="M9 8h6M9 12h6"/></symbol>'
    '<symbol id="i-mapa" viewBox="0 0 24 24">'
    '<path d="M12 21s6.5-6.1 6.5-10.5a6.5 6.5 0 10-13 0C5.5 14.9 12 21 12 21z"/>'
    '<circle cx="12" cy="10.5" r="2.4"/></symbol>'
    '<symbol id="i-sair" viewBox="0 0 24 24">'
    '<path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4M16 17l5-5-5-5M21 12H9"/></symbol>'
    '</defs></svg>')


def _ic(nome: str, cls: str = "ic") -> str:
    return f'<svg class="{cls}"><use href="#i-{nome}"/></svg>'


# ================================================================== helpers
def _page(title: str, corpo: str) -> HTMLResponse:
    """Documento do protótipo. Sem manifest e sem service worker de propósito: isto
    não é pra instalar, e o SW do /cockpit cacheia sob o mesmo escopo."""
    return HTMLResponse(
        "<!doctype html><html lang=pt-br><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1,viewport-fit=cover'>"
        "<meta name=theme-color content='#0A0F0C'><meta name=robots content=noindex>"
        f"<title>{esc(title)} · Zaq</title>{_CSS}</head><body>{_ICONES}"
        f"<div class=wrap><div class=glow></div>{corpo}</div></body></html>")


def _brl(centavos, *, centavos_visiveis: bool = False) -> str:
    """R$ com separador de milhar. `cockpit_dono._reais` arredonda pra 'R$ 12 mil' —
    serve pra KPI de time, mas não pra dizer a alguém quanto ele vai receber."""
    v = int(centavos or 0) / 100
    s = f"{v:,.2f}" if centavos_visiveis else f"{v:,.0f}"
    return "R$ " + s.replace(",", "X").replace(".", ",").replace("X", ".")


def _data(dt) -> str:
    """timestamptz → 09/08/2026, em horário de Brasília. Sem isso o datetime cru do
    banco (com microssegundos e fuso) vaza pra tela."""
    if not hasattr(dt, "strftime"):
        return ""
    from finance import agenda as ag
    try:
        return dt.astimezone(ag.BRT).strftime("%d/%m/%Y")
    except (ValueError, TypeError):
        return dt.strftime("%d/%m/%Y")


_TEMP = {"quente": "var(--coral)", "morno": "var(--ambar)", "frio": "var(--azul)"}
# cockpit_dono.leads/vendedor devolvem `temp_cor` já resolvido na paleta LEGADA
# (painel_prospeccao.TEMP_COR). Traduz pro vocabulário novo em vez de deixar dois
# azuis diferentes convivendo na mesma tela.
_TEMP_LEGADO = {"#e0574f": "var(--coral)", "#e0a33e": "var(--ambar)", "#5b9bd5": "var(--azul)"}


def _cor_temp(hexa: str) -> str:
    return _TEMP_LEGADO.get((hexa or "").strip().lower(), "var(--azul)")


def _flash(request: Request) -> str:
    """Recado da última ação. Usa as MESMAS chaves de sessão do /cockpit (ck_ok /
    ck_err), então mensagem posta por qualquer um dos dois apps aparece aqui."""
    ok = request.session.pop("ck_ok", None)
    err = request.session.pop("ck_err", None)
    txt, cls = (ok, "ok") if ok else (err, "err") if err else (None, "")
    return f"<div class='flash {cls}'>{esc(txt)}</div>" if txt else ""


def _previa(voltar: str = "/cockpit") -> str:
    return ("<div class=previa>Prévia do app novo · "
            f"<a href='{esc(voltar)}'>abrir o atual</a></div>")


def _hdr(titulo: str, sub: str = "", *, voltar: str = "", direita: str = "",
         inicial: str = "", href_inicial: str = "") -> str:
    esq = (f"<a class=bk href='{esc(voltar)}'>{_ic('volta')}</a>" if voltar else "")
    if not esq and inicial:
        alvo = href_inicial or f"{_BASE}/perfil"
        esq = f"<a class=av href='{esc(alvo)}'>{esc(inicial)}</a>"
    return (f"<div class=hdr>{esq}<div class=tt><b>{esc(titulo)}</b>"
            + (f"<small>{esc(sub)}</small>" if sub else "") + f"</div>{direita}</div>")


def _abas(itens, ativo: str) -> str:
    """Barra de abas. É o que faltava no app do vendedor — ele tinha uma tela só."""
    out = []
    for chave, icone, rotulo, href in itens:
        on = " class=on" if chave == ativo else ""
        out.append(f"<a{on} href='{esc(href)}'>{_ic(icone)}<span>{esc(rotulo)}</span></a>")
    return "<div class=tabs>" + "".join(out) + "</div>"


def _abas_vend(ativo: str) -> str:
    return _abas([("fila", "fila", "Fila", _BASE),
                  ("agenda", "agenda", "Agenda", f"{_BASE}/agenda"),
                  ("orcamentos", "orc", "Propostas", f"{_BASE}/orcamentos"),
                  ("resultado", "resultado", "Resultado", f"{_BASE}/resultado"),
                  ("perfil", "perfil", "Perfil", f"{_BASE}/perfil")], ativo)


def _abas_dono(ativo: str) -> str:
    # Perfil sai da barra e vira o avatar do topo (igual no app do vendedor): com
    # seis abas os rótulos não cabem numa tela de 390px.
    return _abas([("visao", "visao", "Visão", _BASE),
                  ("placar", "placar", "Placar", f"{_BASE}/equipe/placar"),
                  ("leads", "leads", "Leads", f"{_BASE}/equipe/leads"),
                  ("orcamentos", "orc", "Propostas", f"{_BASE}/orcamentos"),
                  ("ativ", "ativ", "Atividade", f"{_BASE}/equipe/atividade")], ativo)


def _hdr_dono(conta_id: int, titulo: str, sub: str = "", voltar: str = "") -> str:
    """Topo das telas de gestão: o selo da empresa à esquerda abre o perfil (é por
    onde ele sai do app — o Cockpit atual não tem nem tela de perfil pro dono)."""
    marca = _marca_conta(conta_id)
    return _hdr(titulo, sub, voltar=voltar, inicial=marca["iniciais"],
                href_inicial=f"{_BASE}/perfil")


def _marca_conta(conta_id: int):
    from finance import empresa as emp
    return emp.marca_empresa(get_pool(), conta_id)


def _selo(conta_id: int) -> str:
    """Selo da empresa no topo. White-label: a cor é do cliente (finance/marca.py) e
    fica confinada ao selo — o resto do app é a marca do Zaq."""
    from finance.marca import cabecalho_html
    return cabecalho_html(_marca_conta(conta_id), px=28, raio=8, alinhar="right",
                          cor_nome="var(--text)")


# ================================================================== VENDEDOR
@router.get("/cockpit/novo", response_class=HTMLResponse)
def novo_inicio(request: Request, meus: str = ""):
    """Bifurca igual ao app atual: dono/gestor cai na visão de equipe, vendedor na fila.

    `?meus=1` é a saída pro gestor que TAMBÉM vende: no app atual ele nunca chega na
    própria caixa, porque `_gerencia` é testado antes de `_sessao`.
    """
    g = _gerencia(request)
    if g and not (meus and g[1]):
        return _dono_visao(request, g[0])
    sess = _sessao(request)
    if not sess:
        return RedirectResponse("/cockpit/login", status_code=303)
    return _fila(request, sess[0], sess[1], gestor=bool(g))


def _fila(request: Request, conta_id: int, membro_id: int, *, gestor: bool = False) -> HTMLResponse:
    pool = get_pool()
    leads = ck.leads_do_vendedor(pool, conta_id, membro_id)
    p = ck.perfil(pool, conta_id, membro_id)
    vez = sum(1 for l in leads if not l["ia"])

    cartoes = []
    for l in leads:
        chip = ("<span class='chip ia'>IA</span>" if l["ia"]
                else "<span class='chip voce'>sua vez</span>")
        cartoes.append(
            f"<a class=lead href='{_BASE}/lead/{l['id']}'>"
            f"<span class=dot style='background:{_TEMP.get(l['temperatura'], 'var(--azul)')}'></span>"
            f"<span class=mid><span class=top><span class=emp>{esc(l['empresa'])}</span>{chip}</span>"
            f"<span class=snip>{esc(l['snip'])}</span></span></a>")
    lista = "".join(cartoes) or (
        "<div class=vazio><div class=big>◎</div><b>Fila zerada</b>"
        "Nenhum lead aberto agora. Quando cair um novo no rodízio, você é avisado.</div>")

    volta = ("<div class=bloco style='margin-top:.9rem'>"
             f"<a class='btn ghost' href='{_BASE}'>Ver a visão da equipe</a></div>") if gestor else ""
    corpo = (_previa()
             + _hdr("Meus leads", f"{len(leads)} abertos · {vez} sua vez",
                    inicial=_ini(p["nome"]), direita=_selo(conta_id))
             + _flash(request)
             + f"<div class=scroll>{lista}{volta}</div>"
             + _abas_vend("fila"))
    return _page("Meus leads", corpo)


@router.get("/cockpit/novo/agenda", response_class=HTMLResponse)
def novo_agenda(request: Request):
    """A agenda do vendedor. No app atual ele marca a visita e ela some — não existe
    tela nenhuma que devolva 'o que eu tenho pra hoje'."""
    sess = _sessao(request)
    if not sess:
        return RedirectResponse("/cockpit/login", status_code=303)
    conta_id, membro_id = sess
    visitas = ck.visitas_do_vendedor(get_pool(), conta_id, membro_id)
    hoje = [v for v in visitas if v["hoje"]]

    def bloco(v):
        acoes = []
        if v["lead_id"]:
            acoes.append(f"<a href='{_BASE}/lead/{v['lead_id']}'>{_ic('ficha', 'ic p')} Lead</a>")
        if v["maps"]:
            acoes.append(f"<a href='{esc(v['maps'])}' target=_blank rel=noopener>{_ic('mapa', 'ic p')} Mapa</a>")
        if v["zap"]:
            acoes.append(f"<a href='{esc(v['zap'])}' target=_blank rel=noopener>{_ic('zap', 'ic p')} Avisar</a>")
        if v["ics_url"]:
            acoes.append(f"<a href='{esc(v['ics_url'])}'>{_ic('agenda', 'ic p')} Calendário</a>")
        return (f"<div class='vis{' hoje' if v['hoje'] else ''}'>"
                f"<div class=quando><div class=h>{esc(v['hora'])}</div><div class=d>{esc(v['dia'])}</div></div>"
                f"<div class=mid><b>{esc(v['titulo'])}</b>"
                + (f"<div class=loc>{esc(v['local'])}</div>" if v["local"] else "")
                + (f"<div class=acoes>{''.join(acoes)}</div>" if acoes else "")
                + "</div></div>")

    if visitas:
        miolo = ("<div class=eyebrow>Hoje</div>"
                 + ("".join(bloco(v) for v in hoje) if hoje
                    else "<div class=fonte>Nada marcado pra hoje.</div>"))
        depois = [v for v in visitas if not v["hoje"]]
        if depois:
            miolo += "<div class=eyebrow>Próximos dias</div>" + "".join(bloco(v) for v in depois)
    else:
        miolo = ("<div class=vazio><div class=big>◷</div><b>Nenhuma visita marcada</b>"
                 "Abra um lead e toque em <b>Agendar visita</b> — ela aparece aqui.</div>")

    corpo = (_previa() + _hdr("Minha agenda", f"{len(hoje)} hoje · {len(visitas)} nos próximos 14 dias")
             + f"<div class=scroll>{miolo}</div>" + _abas_vend("agenda"))
    return _page("Minha agenda", corpo)


@router.get("/cockpit/novo/resultado", response_class=HTMLResponse)
def novo_resultado(request: Request):
    """O que faltava: o vendedor vendo o próprio dinheiro.

    `membros.comissao_pct` existe desde a migração 137, mas só o dono via, no
    relatório do painel. Sem % configurada a linha de comissão simplesmente não
    aparece — melhor faltar do que inventar número.
    """
    sess = _sessao(request)
    if not sess:
        return RedirectResponse("/cockpit/login", status_code=303)
    conta_id, membro_id = sess
    periodo = request.query_params.get("p", "mes")
    if periodo not in ("hoje", "semana", "mes"):
        periodo = "mes"
    r = ck.remuneracao(get_pool(), conta_id, membro_id, periodo)

    def seg(k, lab):
        return f"<a class='{'on' if periodo == k else ''}' href='{_BASE}/resultado?p={k}'>{lab}</a>"

    comissao = ""
    if r["comissao_centavos"] is not None:
        pct = f"{r['comissao_pct']:g}".replace(".", ",")
        comissao = (f"<div class='kpi hero'><div class=v>{esc(_brl(r['comissao_centavos'], centavos_visiveis=True))}</div>"
                    f"<div class=l>Sua comissão</div><div class=d>{pct}% do que você fechou</div></div>")
    else:
        comissao = ("<div class=bloco><div class=card style='font-size:.82rem;color:var(--text-dim)'>"
                    "Sua <b>% de comissão</b> ainda não foi configurada. Quem define é o dono, "
                    "em Equipe → comissão.</div></div>")

    pos = (f"<div class=kpi><div class=v>{r['posicao']}º</div><div class=l>No placar</div>"
           f"<div class=d>entre {r['total_equipe']} da equipe</div></div>") if r["posicao"] else ""

    corpo = (_previa()
             + _hdr("Meu resultado", "o que você fechou")
             + f"<div class=scroll><div class=seg>{seg('hoje','Hoje')}{seg('semana','Semana')}{seg('mes','Mês')}</div>"
             + comissao
             + "<div class=kpis>"
             + f"<div class=kpi><div class=v>{esc(_brl(r['fechado_centavos']))}</div>"
               f"<div class=l>Fechado</div><div class=d>{r['ganhos']} negócio(s)</div></div>"
             + f"<div class=kpi><div class=v>{esc(r['conversao'])}</div><div class=l>Conversão</div>"
               "<div class=d>ganhos vs. perdidos</div></div>"
             + f"<div class=kpi><div class=v>{r['fila']}</div><div class=l>Na fila</div>"
               "<div class=d>leads abertos com você</div></div>"
             + f"<div class=kpi><div class=v>{esc(r['resp'])}</div><div class=l>Resposta</div>"
               "<div class=d>média de 30 dias</div></div>"
             + pos + "</div>"
             + "<div class=fonte>De onde vem: soma do <b>valor estimado</b> dos leads que você "
               "marcou como ganho no Cockpit — a mesma base do placar do dono. O relatório de "
               "comissão do painel usa outra base (lançamentos), então os dois ainda podem "
               "divergir.</div>"
             + "</div>" + _abas_vend("resultado"))
    return _page("Meu resultado", corpo)


# ------------------------------------------------------------------ propostas
_STATUS_SEG = [("", "Todas"), ("enviado", "Enviadas"), ("negociando", "Negociando"),
               ("aprovada", "Aprovadas"), ("fechado", "Fechadas"), ("perdido", "Perdidas")]
_STATUS_CLS = {"aprovada": "neon", "fechado": "neon", "perdido": "err",
               "negociando": "voce", "enviado": "", "rascunho": ""}


@router.get("/cockpit/novo/orcamentos", response_class=HTMLResponse)
def novo_orcamentos(request: Request, s: str = "", v: str = ""):
    """Mesma aba, dois alcances: o vendedor vê as propostas DELE (é o escopo que
    painel_servicos já aplica por `criado_por`); dono/gestor veem a carteira toda,
    com filtro por vendedor."""
    g = _gerencia(request)
    sess = _sessao(request)
    if not g and not sess:
        return RedirectResponse("/cockpit/login", status_code=303)
    pool = get_pool()
    gestao = bool(g)
    conta_id = g[0] if gestao else sess[0]
    vend_i = int(v) if v.isdigit() else None
    lista = ck.orcamentos(pool, conta_id,
                          membro_id=None if gestao else sess[1],
                          status=s, vendedor_id=vend_i if gestao else None)

    def url(**over):
        p = {"s": s, "v": v}
        p.update(over)
        q = "&".join(f"{k}={val}" for k, val in p.items() if val)
        return f"{_BASE}/orcamentos" + (("?" + q) if q else "")

    def chip(on, label, href):
        return f"<a class='{'on' if on else ''}' href='{esc(href)}'>{esc(label)}</a>"

    filtros = ("<div class=filt><span class=lbl>Status</span>"
               + "".join(chip(s == k, lab, url(s=k)) for k, lab in _STATUS_SEG) + "</div>")
    if gestao:
        vends = cd.placar(pool, conta_id)
        filtros += ("<div class=filt><span class=lbl>Quem fez</span>"
                    + chip(not v, "Todos", url(v=""))
                    + "".join(chip(v == str(x["id"]), x["nome"].split(" ")[0], url(v=str(x["id"])))
                              for x in vends) + "</div>")

    linhas = []
    for o in lista:
        val = _brl(o["setup_centavos"] + o["mensal_centavos"])
        sub = o["vendedor"] if gestao else _data(o["criado_em"])
        linhas.append(
            f"<a class=lead href='{_BASE}/orcamentos/{o['id']}'>"
            f"<span class=mid><span class=top><span class=emp>{esc(o['titulo'])}</span>"
            f"<span class='chip {_STATUS_CLS.get(o['status'], '')}'>{esc(o['status_rot'])}</span></span>"
            f"<span class=snip>{esc(val)} · {esc(sub)}</span></span></a>")
    if lista:
        total = sum(o["setup_centavos"] + o["mensal_centavos"] for o in lista)
        miolo = (f"<div class=fonte style='margin-top:.7rem'>{len(lista)} proposta(s) · "
                 f"{esc(_brl(total))} em jogo</div>" + "".join(linhas))
    else:
        miolo = ("<div class=vazio><div class=big>◻</div><b>Nenhuma proposta aqui</b>"
                 + ("Ninguém do time montou proposta com esse filtro." if gestao
                    else "Abra um lead e toque em <b>Orçamento</b> — ela aparece aqui pra você mandar.")
                 + "</div>")

    if gestao:
        corpo = (_previa("/painel/servicos")
                 + _hdr_dono(conta_id, "Propostas", "a carteira do time")
                 + _flash(request) + filtros
                 + f"<div class=scroll>{miolo}</div>" + _abas_dono("orcamentos"))
    else:
        p = ck.perfil(pool, conta_id, sess[1])
        corpo = (_previa()
                 + _hdr("Minhas propostas", "manda pro cliente por aqui",
                        inicial=_ini(p["nome"]))
                 + _flash(request) + filtros
                 + f"<div class=scroll>{miolo}</div>" + _abas_vend("orcamentos"))
    return _page("Propostas", corpo)


@router.get("/cockpit/novo/orcamentos/{orc_id}", response_class=HTMLResponse)
def novo_orcamento(request: Request, orc_id: int):
    g = _gerencia(request)
    sess = _sessao(request)
    if not g and not sess:
        return RedirectResponse("/cockpit/login", status_code=303)
    gestao = bool(g)
    conta_id = g[0] if gestao else sess[0]
    o = ck.orcamento(get_pool(), conta_id, orc_id, membro_id=None if gestao else sess[1])
    if not o:
        return RedirectResponse(f"{_BASE}/orcamentos", status_code=303)

    def _linha_item(it):
        setup, mensal = int(it.get("setup") or 0), int(it.get("mensal") or 0)
        partes = []
        if setup:
            partes.append(_brl(setup * 100))
        if mensal:
            partes.append(_brl(mensal * 100) + "/mês")
        return ("<div class=ficha-l><span>" + esc(it.get("nome", "")) + "</span><b>"
                + esc(" + ".join(partes) or "—") + "</b></div>")

    itens = "".join(_linha_item(it) for it in o["itens"])

    # É isto que o vendedor precisa: mandar a proposta pro cliente em um toque.
    # O link é o /proposta/<token> que o sistema já usa — o cliente abre, vê com a
    # marca da empresa e aprova online.
    envio = []
    if o["zap"]:
        envio.append(f"<a class=btn href='{esc(o['zap'])}' target=_blank rel=noopener>"
                     f"{_ic('zap', 'ic p')} Mandar no WhatsApp</a>")
    if o["lead_id"] and not gestao:
        envio.append(f"<form method=post action='{_BASE}/orcamentos/{orc_id}/enviar'>"
                     "<button class='btn ghost' style='margin-top:.5rem' type=submit>"
                     "Enviar na conversa do lead</button></form>")
    if o["link"]:
        envio.append(f"<a class='btn ghost' style='margin-top:.5rem' href='{esc(o['link'])}' "
                     f"target=_blank rel=noopener>Abrir a proposta como o cliente vê</a>")
        envio.append("<div class=copiar><input value='" + esc(o["link"]) + "' readonly "
                     "onclick='this.select()'><button type=button onclick=\"navigator.clipboard"
                     ".writeText(this.previousElementSibling.value);this.textContent='Copiado'\">"
                     "Copiar</button></div>")

    def opt(k, lab):
        return f"<option value='{k}'{' selected' if o['status'] == k else ''}>{esc(lab)}</option>"
    mover = ("<div class=eyebrow>Mover no funil</div><div class=bloco>"
             f"<form method=post action='{_BASE}/orcamentos/{orc_id}/status' class=linhaform>"
             "<select name=status>"
             + "".join(opt(k, ck._ROT_ORC.get(k, k.title())) for k in ck.STATUS_ORC)
             + "</select><button class=btn style='width:auto;padding:.55rem 1rem' type=submit>"
             "Salvar</button></form></div>")

    # "editar ou qualquer coisa": o editor completo já existe no painel e aceita
    # deep link (?abrir=<id>) — não faz sentido reconstruir o formulário no celular.
    editar = (f"<div class=bloco><a class='btn ghost' href='/painel/servicos?abrir={orc_id}' "
              "target=_blank rel=noopener>Editar no painel (itens, valores, escopo)</a></div>"
              if gestao else "")

    aprovada = ""
    if o["aprovada_em"]:
        aprovada = ("<div class=bloco><div class='card' style='border-color:#1e4a3a;"
                    "background:rgba(37,211,102,.08)'><b style='color:var(--neon)'>Cliente aprovou</b>"
                    f"<div class=mut style='font-size:.8rem'>{esc(o['aprovada_por'])}"
                    f" · {esc(_data(o['aprovada_em']))}</div></div></div>")

    ficha = [("Cliente", o["cliente"]), ("Empresa", o["empresa"]), ("CNPJ", o["cnpj"]),
             ("WhatsApp", o["whatsapp"]), ("E-mail", o["email"]),
             ("Quem fez", o["vendedor"] if gestao else ""), ("Criada em", _data(o["criado_em"]))]
    ficha_html = "".join(f"<div class=ficha-l><span>{esc(k)}</span><b>{esc(v)}</b></div>"
                         for k, v in ficha if v)

    total = _brl(o["setup_centavos"] + o["mensal_centavos"])
    corpo = (_previa("/painel/servicos" if gestao else "/cockpit")
             + _hdr(o["titulo"], o["status_rot"], voltar=f"{_BASE}/orcamentos")
             + _flash(request)
             + "<div class=scroll>"
             + "<div class=kpis style='margin-top:.9rem'>"
             + f"<div class='kpi hero'><div class=v>{esc(total)}</div><div class=l>Valor da proposta</div>"
             + (f"<div class=d>{esc(_brl(o['setup_centavos']))} entrada + "
                f"{esc(_brl(o['mensal_centavos']))}/mês</div>" if o["mensal_centavos"] else "")
             + "</div></div>"
             + aprovada
             + (f"<div class=eyebrow>Mandar pro cliente</div><div class=bloco>{''.join(envio)}</div>"
                if envio else "")
             + mover + editar
             + (f"<div class=eyebrow>O que entra</div><div class=bloco><div class=card>{itens}</div></div>"
                if itens else "")
             + (f"<div class=eyebrow>Cliente</div><div class=bloco><div class=card>{ficha_html}</div></div>"
                if ficha_html else "")
             + "</div>"
             + (_abas_dono("orcamentos") if gestao else _abas_vend("orcamentos")))
    return _page(o["titulo"], corpo)


@router.post("/cockpit/novo/orcamentos/{orc_id}/status")
def novo_orcamento_status(request: Request, orc_id: int, status: str = Form(...)):
    g = _gerencia(request)
    sess = _sessao(request)
    if not g and not sess:
        return RedirectResponse("/cockpit/login", status_code=303)
    conta_id = g[0] if g else sess[0]
    r = ck.mudar_status_orcamento(get_pool(), conta_id, orc_id, status,
                                  membro_id=None if g else sess[1])
    request.session["ck_ok" if r.get("ok") else "ck_err"] = (
        r.get("msg", "Feito ✓") if r.get("ok") else r.get("erro", "Não deu certo."))
    return RedirectResponse(f"{_BASE}/orcamentos/{orc_id}", status_code=303)


@router.post("/cockpit/novo/orcamentos/{orc_id}/enviar")
def novo_orcamento_enviar(request: Request, orc_id: int):
    """Manda o link da proposta na conversa do lead, pelo WhatsApp da empresa —
    mesmo caminho que o botão de proposta na tela do lead já usa."""
    sess = _sessao(request)
    if not sess:
        return RedirectResponse("/cockpit/login", status_code=303)
    conta_id, membro_id = sess
    o = ck.orcamento(get_pool(), conta_id, orc_id, membro_id=membro_id)
    if not o or not o["lead_id"] or not o["link"]:
        request.session["ck_err"] = "Essa proposta não está ligada a um lead com conversa."
        return RedirectResponse(f"{_BASE}/orcamentos/{orc_id}", status_code=303)
    r = ck.enviar_proposta_conversa(get_pool(), conta_id, membro_id, o["lead_id"], o["link"])
    request.session["ck_ok" if r.get("ok") else "ck_err"] = (
        "Proposta enviada na conversa ✓" if r.get("ok") else r.get("erro", "Não consegui enviar."))
    return RedirectResponse(f"{_BASE}/orcamentos/{orc_id}", status_code=303)


@router.get("/cockpit/novo/perfil", response_class=HTMLResponse)
def novo_perfil(request: Request):
    """Perfil do vendedor — e, pro dono/gestor, o perfil que o app atual não tem.

    No /cockpit atual `/cockpit/perfil` exige `membro_id`, e o dono titular não tem:
    ele cai no login. Aqui a tela do dono se apoia em `_gerencia`, que aceita
    `membro_id` nulo.
    """
    sess = _sessao(request)
    if sess:
        return _perfil_vendedor(request, sess[0], sess[1])
    g = _gerencia(request)
    if g:
        return _perfil_dono(g[0], g[1])
    return RedirectResponse("/cockpit/login", status_code=303)


def _perfil_vendedor(request: Request, conta_id: int, membro_id: int) -> HTMLResponse:
    p = ck.perfil(get_pool(), conta_id, membro_id)

    def tgl(rotulo, sub, ligado, acao):
        # posta no endpoint que já existe no /cockpit e volta pra cá
        return (f"<form method=post action='{acao}' style='margin:0'>"
                f"<input type=hidden name=on value='{0 if ligado else 1}'>"
                "<div class=card style='display:flex;align-items:center;gap:1rem;margin-bottom:.6rem'>"
                f"<div style='flex:1'><b style='font-size:.9rem'>{esc(rotulo)}</b>"
                f"<div class=mut style='font-size:.76rem'>{esc(sub)}</div></div>"
                f"<button type=submit style='width:auto;flex-shrink:0' class='btn {'' if ligado else 'ghost'}'>"
                f"{'Ligado' if ligado else 'Desligado'}</button></div></form>")

    corpo = (_previa()
             + _hdr("Meu perfil", "vendedor")
             + _flash(request)
             + "<div class=scroll>"
             + "<div class=bloco><div class=card style='display:flex;align-items:center;gap:.9rem'>"
             + f"<span class=av style='width:46px;height:46px;font-size:1.1rem'>{esc(_ini(p['nome']))}</span>"
             + f"<div style='min-width:0'><b>{esc(p['nome'])}</b>"
               f"<div class=mut style='font-size:.78rem;word-break:break-word'>{esc(p['email'])}"
             + (f" · {esc(p['whatsapp'])}" if p["whatsapp"] else "") + "</div></div></div></div>"
             + "<div class=eyebrow>Preferências</div><div class=bloco>"
             + tgl("Notificações push", "avisar quando cair um lead", p["push_ativo"], f"{_BASE}/perfil/push")
             + tgl("Receber no rodízio", "desligue pra pausar leads novos", not p["pausado"], f"{_BASE}/perfil/rodizio")
             + "</div>"
             + f"<div class=bloco><a class='btn ghost' href='/cockpit/sair'>{_ic('sair', 'ic p')} Sair</a></div>"
             + "</div>" + _abas_vend("perfil"))
    return _page("Meu perfil", corpo)


def _perfil_dono(conta_id: int, membro_id: int | None) -> HTMLResponse:
    marca = _marca_conta(conta_id)
    # o dono titular não é membro da equipe, então não tem fila própria — só o
    # gestor (que também vende) tem caixa pra abrir
    minha_caixa = (f"<a class='btn ghost' href='{_BASE}?meus=1'>Ver a minha caixa de leads</a>"
                   if membro_id else "")
    corpo = (_previa()
             + _hdr("Perfil", "dono · gestão da equipe")
             + "<div class=scroll>"
             + "<div class=bloco><div class=card style='display:flex;align-items:center;gap:.9rem'>"
             + f"<span class=av style='width:46px;height:46px;font-size:1.1rem'>{esc(marca['iniciais'])}</span>"
             + f"<div style='min-width:0'><b>{esc(marca['nome'])}</b>"
               "<div class=mut style='font-size:.78rem'>você acompanha o time por aqui</div></div></div></div>"
             + "<div class=eyebrow>Atalhos</div><div class=bloco>"
             + "<a class='btn ghost' style='margin-bottom:.5rem' href='/painel/equipe'>Gerir a equipe no painel</a>"
             + "<a class='btn ghost' style='margin-bottom:.5rem' href='/painel/prospeccao'>Abrir o funil completo</a>"
             + minha_caixa + "</div>"
             + f"<div class=bloco><a class='btn ghost' href='/cockpit/sair'>{_ic('sair', 'ic p')} Sair</a></div>"
             + "</div>" + _abas_dono("perfil"))
    return _page("Perfil", corpo)


# ------------------------------------------------------------------ lead
@router.get("/cockpit/novo/lead/{lead_id}", response_class=HTMLResponse)
def novo_lead(request: Request, lead_id: int):
    """Uma rota, dois papéis. Se o lead é do vendedor logado, abre a tela de trabalho
    (chat em primeiro plano). Se não é dele mas quem olha é gestão, abre a visão de
    gestão — em vez de chutar pro /painel/prospeccao como o app atual faz."""
    sess = _sessao(request)
    if sess:
        d = ck.lead_do_vendedor(get_pool(), sess[0], sess[1], lead_id)
        if d:
            return _lead_vendedor(request, lead_id, d)
    g = _gerencia(request)
    if g:
        return _lead_gestor(request, g[0], lead_id)
    return RedirectResponse("/cockpit/login", status_code=303)


def _lead_vendedor(request: Request, lead_id: int, d: dict) -> HTMLResponse:
    sub = " · ".join(x for x in [d.get("cidade") or "", d.get("uf") or ""] if x) or (d.get("doc_fmt") or "")

    bolhas = []
    for m in d["mensagens"]:
        who = m["who"]
        rot = ("<div class=who>Agente</div>" if who == "ia"
               else "<div class=who>Você</div>" if who == "out" else "")
        bolhas.append(f"<div class='bub {esc(who)}'>{rot}{esc(m['texto'])}</div>")
    if d["ia"]:
        bolhas.insert(0, "<div class=aviso>O agente está atendendo. Toque em "
                         "<b>Assumir</b> pra responder você.</div>")
    chat = "".join(bolhas) or "<div class=aviso>Sem mensagens ainda.</div>"

    if d["ia"]:
        acao = (f"<form method=post action='{_BASE}/lead/{lead_id}/assumir'>"
                "<button class=btn type=submit>Assumir a conversa</button></form>")
    else:
        acao = (f"<form class=composer method=post action='{_BASE}/lead/{lead_id}/mensagem'>"
                "<input name=texto placeholder='Responder…' required autocomplete=off>"
                "<button type=submit aria-label=Enviar>&#10148;</button></form>")

    tel, zap = d.get("tel_link") or "", d.get("zap_link") or ""
    atalhos = (
        (f"<a href='{esc(tel)}'>{_ic('ligar', 'ic p')} Ligar</a>" if tel
         else f"<span class=off>{_ic('ligar', 'ic p')} Ligar</span>")
        + (f"<a href='{esc(zap)}' target=_blank rel=noopener>{_ic('zap', 'ic p')} WhatsApp</a>" if zap
           else f"<span class=off>{_ic('zap', 'ic p')} WhatsApp</span>")
        + f"<a class=orc href='/cockpit/lead/{lead_id}/orcamento'>{_ic('orc', 'ic p')} Orçamento</a>"
        + f"<a class=vis2 href='/cockpit/lead/{lead_id}/visita'>{_ic('agenda', 'ic p')} Visita</a>")

    etapas = "".join(
        f"<form method=post action='{_BASE}/lead/{lead_id}/etapa'>"
        f"<input type=hidden name=etapa value='{esc(e['chave'])}'>"
        f"<button class='{'on' if d['status'] == e['chave'] else ''}' type=submit>{esc(e['rotulo'])}</button>"
        "</form>" for e in d["etapas"])

    motivos = "".join(f"<option>{esc(m)}</option>" for m in
                      ("Preço", "Sem retorno", "Comprou concorrente", "Fora do perfil", "Sem interesse"))
    ficha = [("Contato", d.get("contato")), ("Cargo", d.get("cargo")),
             (d.get("doc_rot") or "Doc", d.get("doc_fmt")), ("Segmento", d.get("segmento")),
             ("Telefone", d.get("telefone")), ("E-mail", d.get("email"))]
    ficha_html = "".join(f"<div class=ficha-l><span>{esc(k)}</span><b>{esc(v)}</b></div>"
                         for k, v in ficha if v)

    # A folha só sobe com :target — sem JS, então funciona igual ao resto do app,
    # que é todo form + redirect.
    folha = (
        "<div class=folha id=acoes><div class=puxa></div>"
        f"<div class=grade>{atalhos}</div>"
        f"<h3>Etapa no funil</h3><div class=etapas>{etapas}</div>"
        f"<h3>Fechar</h3>"
        f"<form method=post action='{_BASE}/lead/{lead_id}/fechar' style='margin-bottom:.5rem'>"
        "<input type=hidden name=tipo value=ganho><button class=btn type=submit>Marcar como ganho</button></form>"
        f"<form method=post action='{_BASE}/lead/{lead_id}/fechar' class=linhaform>"
        "<input type=hidden name=tipo value=perdido>"
        f"<select name=motivo><option value=''>Motivo (opcional)</option>{motivos}</select>"
        "<button class='btn perigo' style='width:auto;padding:.55rem .9rem' type=submit>Perdido</button></form>"
        + (f"<h3>Ficha</h3>{ficha_html}" if ficha_html else "")
        + "</div><a class=fbg href='#fechar' aria-label='Fechar'></a>")

    chip = ("<span class='chip ia'>IA</span>" if d["ia"] else "<span class='chip voce'>você</span>")
    corpo = (_previa(f"/cockpit/lead/{lead_id}")
             + _hdr(d["empresa"], sub, voltar=_BASE, direita=chip)
             + _flash(request)
             + f"<div class=chat>{chat}</div>"
             + f"<div class=rodape>{acao}"
             + "<a class='btn ghost' style='margin-top:.5rem' href='#acoes'>Ficha, funil e fechamento</a>"
             + "</div>" + folha)
    return _page(d["empresa"], corpo)


def _lead_gestor(request: Request, conta_id: int, lead_id: int) -> HTMLResponse:
    """A tela que não existia. No app atual o gestor toca num lead da equipe e cai em
    /painel/prospeccao/{id} — o painel desktop, no meio do celular."""
    from web.painel_prospeccao import _carrega_alvo
    pool = get_pool()
    d = _carrega_alvo(pool, conta_id, lead_id)
    if not d:
        return RedirectResponse(f"{_BASE}/equipe/leads", status_code=303)
    outros = cd.vendedores_para_reatribuir(pool, conta_id, d.get("vendedor_id") or 0)

    sub = " · ".join(x for x in [d.get("cidade") or "", d.get("uf") or ""] if x) or (d.get("doc_fmt") or "")
    tel, zap = d.get("tel_link") or "", d.get("zap_link") or ""
    atalhos = (
        (f"<a href='{esc(tel)}'>{_ic('ligar', 'ic p')} Ligar</a>" if tel
         else f"<span class=off>{_ic('ligar', 'ic p')} Ligar</span>")
        + (f"<a href='{esc(zap)}' target=_blank rel=noopener>{_ic('zap', 'ic p')} WhatsApp</a>" if zap
           else f"<span class=off>{_ic('zap', 'ic p')} WhatsApp</span>")
        + f"<a href='/painel/prospeccao/{lead_id}'>{_ic('ficha', 'ic p')} Ficha completa</a>"
        + (f"<a href='{esc(d['maps_url'])}' target=_blank rel=noopener>{_ic('mapa', 'ic p')} Mapa</a>"
           if d.get("maps_url") else f"<span class=off>{_ic('mapa', 'ic p')} Mapa</span>"))

    linhas = [("Vendedor", d.get("vendedor_nome") or "Sem dono"),
              ("Etapa", (d.get("status") or "novo").title()),
              ("Temperatura", (d.get("temperatura") or "frio").title()),
              ("Valor estimado", _brl(d.get("valor")) if d.get("valor") else ""),
              ("Contato", d.get("contato")), ("Cargo", d.get("cargo")),
              (d.get("doc_rot") or "Doc", d.get("doc_fmt")), ("Segmento", d.get("segmento")),
              ("Origem", d.get("origem")), ("Último contato", _data(d.get("ultimo_contato_em")))]
    ficha = "".join(f"<div class=ficha-l><span>{esc(k)}</span><b>{esc(v)}</b></div>"
                    for k, v in linhas if v)

    if outros:
        opcoes = "".join(f"<option value='{v['id']}'>{esc(v['nome'])}</option>" for v in outros)
        reatribuir = (
            "<div class=eyebrow>Passar pra outro</div><div class=bloco>"
            f"<form method=post action='{_BASE}/equipe/reatribuir' class=linhaform>"
            f"<input type=hidden name=lead_id value='{lead_id}'>"
            f"<select name=para>{opcoes}</select>"
            "<button class=btn style='width:auto;padding:.55rem 1rem' type=submit>Passar</button>"
            "</form></div>")
    else:
        reatribuir = ""

    corpo = (_previa(f"/painel/prospeccao/{lead_id}")
             + _hdr(d.get("empresa") or "Lead", sub, voltar=f"{_BASE}/equipe/leads")
             + _flash(request)
             + "<div class=scroll>"
             + f"<div class=bloco style='margin-top:.9rem'><div class=grade>{atalhos}</div></div>"
             + f"<div class=eyebrow>O lead</div><div class=bloco><div class=card>{ficha}</div></div>"
             + reatribuir
             + (f"<div class=bloco><div class=card style='font-size:.84rem;color:var(--text-dim)'>"
                f"<b style='color:var(--text)'>Observações</b><br>{esc(d['obs'])}</div></div>"
                if d.get("obs") else "")
             + "</div>" + _abas_dono("leads"))
    return _page(d.get("empresa") or "Lead", corpo)


# ================================================================== GESTOR
def _dono_visao(request: Request, conta_id: int) -> HTMLResponse:
    periodo = request.query_params.get("p", "semana")
    if periodo not in ("hoje", "semana", "mes"):
        periodo = "semana"
    v = cd.visao(get_pool(), conta_id, periodo)
    k = v["kpis"]
    conv = f"{k['conversao']}%" if k["conversao"] is not None else "—"

    def seg(key, lab):
        return f"<a class='{'on' if periodo == key else ''}' href='{_BASE}?p={key}'>{lab}</a>"

    funil = "".join(
        f"<div class=fr><span class=nm>{esc(f['rotulo'])}</span>"
        f"<span class=bar><i style='width:{f['pct']}%'></i></span>"
        f"<span class=qt><b>{f['n']}</b><small>{esc(f['valor'])}</small></span></div>"
        for f in v["funil"])
    a = v["atencao"]
    corpo = (_previa()
             + _hdr_dono(conta_id, "Equipe", "acompanhe o time")
             + f"<div class=scroll><div class=seg>{seg('hoje','Hoje')}{seg('semana','Semana')}{seg('mes','Mês')}</div>"
             + "<div class=kpis>"
             + f"<div class='kpi hero'><div class=v>{esc(k['ganhos_rs'])}</div><div class=l>Fechado no período</div>"
               f"<div class=d>{k['ganhos']} negócio(s) · {conv} de conversão</div></div>"
             + f"<div class=kpi><div class=v>{k['novos']}</div><div class=l>Leads novos</div>"
               "<div class=d>no período</div></div>"
             + f"<div class=kpi><div class=v>{k['com_ia'] + k['com_vend']}</div><div class=l>Em atendimento</div>"
               f"<div class=d>{k['com_ia']} c/ IA · {k['com_vend']} c/ vendedor</div></div></div>"
             + "<div class=eyebrow>Funil do time</div>"
             + f"<div class=bloco><div class=card>{funil}</div></div>"
             + "<div class=eyebrow>Precisa de atenção</div>"
             + "<div class=aten>"
             + f"<div class='at hot'><div class=n>{a['parados']}</div><div class=t>parados há +3 dias</div></div>"
             + f"<div class='at warn'><div class=n>{a['quentes']}</div><div class=t>quentes sem contato hoje</div></div>"
             + f"<div class='at info'><div class=n>{a['propostas']}</div><div class=t>propostas aguardando</div></div>"
             + f"<div class='at info'><div class=n>{a['visitas']}</div><div class=t>visitas hoje</div></div></div>"
             + "</div>" + _abas_dono("visao"))
    return _page("Equipe", corpo)


@router.get("/cockpit/novo/equipe/placar", response_class=HTMLResponse)
def novo_placar(request: Request):
    g = _gerencia(request)
    if not g:
        return RedirectResponse(_BASE, status_code=303)
    lista = cd.placar(get_pool(), g[0])

    # pódio: 2º no lugar da esquerda, 1º no meio, 3º na direita
    podio = ""
    if len(lista) >= 2:
        ordem = [(1, "p2"), (0, "p1"), (2, "p3")]
        blocos = []
        for idx, cls in ordem:
            if idx >= len(lista):
                blocos.append("<div class=pod></div>")
                continue
            v = lista[idx]
            blocos.append(
                f"<a class='pod {cls}' href='{_BASE}/equipe/vendedor/{v['id']}'>"
                f"<span class=av>{esc(v['nome'][:1].upper())}</span>"
                f"<div class=nm>{esc(v['nome'].split(' ')[0])}</div>"
                f"<div class=rs>{esc(v['rs'])}</div>"
                f"<div class=base>{idx + 1}º</div></a>")
        podio = "<div class=podio>" + "".join(blocos) + "</div>"

    resto = lista[3:] if podio else lista
    linhas = "".join(
        f"<a class=linha href='{_BASE}/equipe/vendedor/{v['id']}'>"
        f"<span class=rk>{i + (4 if podio else 1)}</span>"
        f"<span class='av mudo'>{esc(v['nome'][:1].upper())}</span>"
        f"<div class=mid><b>{esc(v['nome'])}</b><div class=sub>"
        f"<span>{v['fila']} na fila</span><span>{v['atendendo']} atend.</span><span>{esc(v['resp'])}</span>"
        + ("<span class=pausado>pausado</span>" if v["pausado"] else "")
        + f"</div></div><div class=rt><span class=g>{esc(v['rs'])}</span>"
          f"<small>{v['ganhos']} ganhos · {esc(v['conversao'])}</small></div></a>"
        for i, v in enumerate(resto))

    if not lista:
        miolo = ("<div class=vazio><div class=big>◇</div><b>Ninguém na equipe ainda</b>"
                 "Convide seu time em Equipe, no painel.</div>")
    else:
        miolo = podio + linhas

    corpo = (_previa("/cockpit/equipe/placar")
             + _hdr_dono(g[0], "Placar", "este mês, por R$ fechado")
             + f"<div class=scroll>{miolo}</div>" + _abas_dono("placar"))
    return _page("Placar", corpo)


@router.get("/cockpit/novo/equipe/atividade", response_class=HTMLResponse)
def novo_atividade(request: Request):
    g = _gerencia(request)
    if not g:
        return RedirectResponse(_BASE, status_code=303)
    feed = cd.atividade(get_pool(), g[0])
    ico = {"ganho": "placar", "perdido": "volta", "visita": "agenda", "prop": "orc"}
    linhas = "".join(
        f"<div class='ev {esc(e['tipo'])}'><span class=ic>{_ic(ico.get(e['tipo'], 'ativ'), 'ic p')}</span>"
        f"<div class=tx>{esc(e['txt'])}</div></div>" for e in feed)
    if not feed:
        linhas = ("<div class=vazio><div class=big>◌</div><b>Sem atividade recente</b>"
                  "Ganhos, perdas, visitas e propostas do time aparecem aqui.</div>")
    corpo = (_previa("/cockpit/equipe/atividade")
             + _hdr_dono(g[0], "Atividade", "o que o time fez")
             + f"<div class=scroll>{linhas}</div>" + _abas_dono("ativ"))
    return _page("Atividade", corpo)


_ETAPA_ROT = {"novo": "Novo", "contatado": "Contatado", "qualificado": "Qualificado",
              "proposta": "Proposta"}
_TEMP_ROT = [("quente", "Quente"), ("morno", "Morno"), ("frio", "Frio")]


@router.get("/cockpit/novo/equipe/leads", response_class=HTMLResponse)
def novo_leads(request: Request, vend: str = "", etapa: str = "", temp: str = ""):
    g = _gerencia(request)
    if not g:
        return RedirectResponse(_BASE, status_code=303)
    pool = get_pool()
    vend_i = int(vend) if vend.isdigit() else None
    lista = cd.leads(pool, g[0], vend_i, etapa, temp)
    filt = cd.filtros_leads(pool, g[0])

    def url(**over):
        p = {"vend": vend, "etapa": etapa, "temp": temp}
        p.update(over)
        q = "&".join(f"{k}={v}" for k, v in p.items() if v)
        return f"{_BASE}/equipe/leads" + (("?" + q) if q else "")

    def chip(on, label, href):
        return f"<a class='{'on' if on else ''}' href='{esc(href)}'>{esc(label)}</a>"

    filtros = (
        "<div class=filt><span class=lbl>Vendedor</span>" + chip(not vend, "Todos", url(vend=""))
        + "".join(chip(vend == str(v["id"]), v["nome"].split(" ")[0], url(vend=str(v["id"])))
                  for v in filt["vendedores"]) + "</div>"
        + "<div class=filt><span class=lbl>Etapa</span>" + chip(not etapa, "Todas", url(etapa=""))
        + "".join(chip(etapa == e, _ETAPA_ROT.get(e, e.title()), url(etapa=e)) for e in filt["etapas"])
        + "</div>"
        + "<div class=filt><span class=lbl>Temp</span>" + chip(not temp, "Todas", url(temp=""))
        + "".join(chip(temp == t, lab, url(temp=t)) for t, lab in _TEMP_ROT) + "</div>")

    # o link fica DENTRO do app — era isto que jogava o gestor no painel desktop
    linhas = "".join(
        f"<a class=lead href='{_BASE}/lead/{l['id']}'>"
        f"<span class=dot style='background:{_cor_temp(l.get('temp_cor'))}'></span>"
        f"<span class=mid><span class=top><span class=emp>{esc(l['empresa'])}</span>"
        + ("<span class='chip ia'>IA</span>" if l["ia"] else "<span class='chip voce'>vend.</span>")
        + f"</span><span class=snip>{esc(l['vendedor'])} · {esc(_ETAPA_ROT.get(l['status'], l['status'].title()))}"
          "</span></span></a>" for l in lista)
    miolo = (f"<div class=fonte style='margin-top:.7rem'>{len(lista)} lead(s)</div>" + linhas) if lista else \
        ("<div class=vazio><div class=big>◌</div><b>Nenhum lead com esses filtros</b>"
         "Tente afrouxar a busca.</div>")

    corpo = (_previa("/cockpit/equipe/leads")
             + _hdr_dono(g[0], "Leads da equipe", "todos os leads abertos")
             + filtros + f"<div class=scroll>{miolo}</div>" + _abas_dono("leads"))
    return _page("Leads da equipe", corpo)


@router.get("/cockpit/novo/equipe/vendedor/{membro_id}", response_class=HTMLResponse)
def novo_vendedor(request: Request, membro_id: int):
    g = _gerencia(request)
    if not g:
        return RedirectResponse(_BASE, status_code=303)
    v = cd.vendedor(get_pool(), g[0], membro_id)
    if not v:
        return RedirectResponse(f"{_BASE}/equipe/placar", status_code=303)

    leads = "".join(
        f"<a class=lead href='{_BASE}/lead/{l['id']}'>"
        f"<span class=dot style='background:{_cor_temp(l.get('temp_cor'))}'></span>"
        f"<span class=mid><span class=top><span class=emp>{esc(l['empresa'])}</span>"
        + ("<span class='chip ia'>IA</span>" if l["ia"] else "<span class='chip voce'>vend.</span>")
        + "</span></span></a>" for l in v["leads"])

    pausar = (f"<form method=post action='{_BASE}/equipe/pausar'>"
              f"<input type=hidden name=membro_id value='{membro_id}'>"
              f"<input type=hidden name=on value='{0 if v['pausado'] else 1}'>"
              f"<button class='btn {'ghost' if not v['pausado'] else ''}' type=submit>"
              f"{'Reativar no rodízio' if v['pausado'] else 'Pausar no rodízio'}</button></form>")

    corpo = (_previa(f"/cockpit/equipe/vendedor/{membro_id}")
             + _hdr(v["nome"], v["papel"], voltar=f"{_BASE}/equipe/placar")
             + _flash(request)
             + "<div class=scroll><div class=kpis style='margin-top:.9rem'>"
             + f"<div class='kpi hero'><div class=v>{esc(v['rs'])}</div><div class=l>Fechado no mês</div>"
               f"<div class=d>{v['ganhos']} negócio(s)</div></div>"
             + f"<div class=kpi><div class=v>{esc(v['conversao'])}</div><div class=l>Conversão</div></div>"
             + f"<div class=kpi><div class=v>{v['fila']}</div><div class=l>Na fila</div></div>"
             + f"<div class=kpi><div class=v>{esc(v['resp'])}</div><div class=l>Resposta</div>"
               "<div class=d>média de 30 dias</div></div></div>"
             + f"<div class=bloco>{pausar}</div>"
             + "<div class=eyebrow>Leads abertos com ele</div>"
             + (leads or "<div class=fonte>Nenhum lead aberto.</div>")
             + "</div>" + _abas_dono("placar"))
    return _page(v["nome"], corpo)


# ================================================================== AÇÕES
# O protótipo NÃO reimplementa escrita: chama exatamente as mesmas funções de
# finance/cockpit.py e finance/cockpit_dono.py que o /cockpit chama — e é lá,
# no motor, que a posse do lead é revalidada no banco a cada ação.
#
# Então por que não postar direto nos endpoints do app atual? Porque eles
# redirecionam de volta pra /cockpit: quem estivesse testando a prévia cairia
# no app velho no primeiro clique. Aqui só o destino do redirect muda.
def _agir(request: Request, lead_id: int, fn, destino: str):
    sess = _sessao(request)
    if not sess:
        return RedirectResponse("/cockpit/login", status_code=303)
    r = fn(get_pool(), sess[0], sess[1], lead_id)
    request.session["ck_ok" if r.get("ok") else "ck_err"] = (
        r.get("msg", "Feito ✓") if r.get("ok") else r.get("erro", "Não deu certo."))
    return RedirectResponse(destino, status_code=303)


@router.post("/cockpit/novo/lead/{lead_id}/mensagem")
def novo_mensagem(request: Request, lead_id: int, texto: str = Form(...)):
    return _agir(request, lead_id,
                 lambda p, c, m, l: {**ck.enviar_mensagem(p, c, m, l, texto), "msg": "Mensagem enviada ✓"},
                 f"{_BASE}/lead/{lead_id}")


@router.post("/cockpit/novo/lead/{lead_id}/etapa")
def novo_etapa(request: Request, lead_id: int, etapa: str = Form(...)):
    return _agir(request, lead_id,
                 lambda p, c, m, l: {**ck.mudar_etapa(p, c, m, l, etapa), "msg": "Etapa atualizada ✓"},
                 f"{_BASE}/lead/{lead_id}")


@router.post("/cockpit/novo/lead/{lead_id}/assumir")
def novo_assumir(request: Request, lead_id: int):
    return _agir(request, lead_id,
                 lambda p, c, m, l: {**ck.assumir(p, c, m, l), "msg": "Você assumiu a conversa ✓"},
                 f"{_BASE}/lead/{lead_id}")


@router.post("/cockpit/novo/lead/{lead_id}/devolver")
def novo_devolver(request: Request, lead_id: int):
    return _agir(request, lead_id,
                 lambda p, c, m, l: {**ck.devolver_ia(p, c, m, l), "msg": "Devolvido pro agente ✓"},
                 f"{_BASE}/lead/{lead_id}")


@router.post("/cockpit/novo/lead/{lead_id}/fechar")
def novo_fechar(request: Request, lead_id: int, tipo: str = Form(...), motivo: str = Form("")):
    sess = _sessao(request)
    if not sess:
        return RedirectResponse("/cockpit/login", status_code=303)
    r = ck.fechar(get_pool(), sess[0], sess[1], lead_id, tipo, motivo)
    if r.get("ok"):                                   # fechou: o lead sai da fila
        request.session["ck_ok"] = "Marcado como Ganho 🎉" if tipo == "ganho" else "Marcado como Perdido."
        return RedirectResponse(_BASE, status_code=303)
    request.session["ck_err"] = r.get("erro", "Não deu certo.")
    return RedirectResponse(f"{_BASE}/lead/{lead_id}", status_code=303)


@router.post("/cockpit/novo/perfil/push")
def novo_push(request: Request, on: str = Form("1")):
    sess = _sessao(request)
    if not sess:
        return RedirectResponse("/cockpit/login", status_code=303)
    ck.set_push(get_pool(), sess[0], sess[1], on == "1")
    return RedirectResponse(f"{_BASE}/perfil", status_code=303)


@router.post("/cockpit/novo/perfil/rodizio")
def novo_rodizio(request: Request, on: str = Form("1")):
    # o toggle diz "receber no rodízio"; pausado = NÃO receber → inverte
    sess = _sessao(request)
    if not sess:
        return RedirectResponse("/cockpit/login", status_code=303)
    ck.set_pausado(get_pool(), sess[0], sess[1], on != "1")
    return RedirectResponse(f"{_BASE}/perfil", status_code=303)


@router.post("/cockpit/novo/equipe/reatribuir")
def novo_reatribuir(request: Request, lead_id: int = Form(...), para: str = Form("")):
    g = _gerencia(request)
    if not g:
        return RedirectResponse(_BASE, status_code=303)
    destino = f"{_BASE}/lead/{lead_id}"
    if para.strip().isdigit():
        r = cd.reatribuir(get_pool(), g[0], lead_id, int(para))
        request.session["ck_ok" if r.get("ok") else "ck_err"] = (
            "Lead passado pro time ✓" if r.get("ok") else r.get("erro", "Não consegui reatribuir."))
    return RedirectResponse(destino, status_code=303)


@router.post("/cockpit/novo/equipe/pausar")
def novo_pausar(request: Request, membro_id: int = Form(...), on: str = Form("1")):
    g = _gerencia(request)
    if not g:
        return RedirectResponse(_BASE, status_code=303)
    cd.pausar(get_pool(), g[0], membro_id, on == "1")
    request.session["ck_ok"] = "Rodízio pausado ✓" if on == "1" else "Vendedor reativado ✓"
    return RedirectResponse(f"{_BASE}/equipe/vendedor/{membro_id}", status_code=303)
