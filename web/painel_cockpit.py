"""Cockpit — o app mobile (PWA) do vendedor e do gestor, servido fora do painel.

Espaço enxuto e mobile pra quem vende: recebe o lead, conversa (pelo chip da
empresa), mexe no funil, monta orçamento, agenda visita e fecha. Entra por LINK
MÁGICO (sem senha) — o gestor gera na Equipe ou a pessoa pede pelo próprio e-mail
em /cockpit/login. Depois disso a sessão (cookie assinado do portal) segura.

Esta versão substituiu a anterior (o "Cockpit do Vendedor" de uma tela só). O que
mudou, e por quê:

  * **Marca.** A versão anterior rodava a paleta legada (#0e0e0f / #1d9e75, fonte
    de sistema) enquanto o site já usava outra. Quem saía de zaq-ia.com e entrava
    no app achava que tinha trocado de produto. Os tokens vêm de web/tema.py — o
    mesmo bloco do painel inteiro. Ícones viram SVG (antes eram emoji, que mudam
    de desenho conforme o aparelho).
  * **O vendedor ganha um app, não uma tela.** Antes era só a fila, sem menu:
    perfil, visitas e resultado ficavam escondidos atrás do avatar. Agora são
    cinco abas — Fila, Agenda, Propostas, Resultado, Perfil.
  * **O vendedor vê dinheiro.** `membros.comissao_pct` existe desde a migração 137
    mas só aparecia pro dono, no relatório do painel. A aba Resultado mostra o que
    ELE fechou e quanto disso é comissão dele — pela mesma conta de
    finance/comissao.py que o relatório usa, pra não haver dois números.
  * **O gestor não é mais expulso.** Antes, tocar num lead da equipe levava pra
    /painel/prospeccao/{id} — o painel desktop, no meio do celular. Aqui o lead
    abre dentro do próprio app, e ele ainda vê a carteira de propostas do time.

Fronteira: aqui só HTTP + HTML. O motor (posse, escopo, escrita) fica em
finance/cockpit.py e finance/cockpit_dono.py, que é o mesmo motor que o painel
usa — o app é uma porta mobile pro mesmo dado, não um silo à parte.

Guards: `_sessao` (vendedor) e `_gerencia` (dono/gestor) são checados aqui, em toda
rota. Isso importa porque o middleware central (web/app.py) só filtra /painel* e
/membros* — /cockpit* passa livre e a checagem é toda daqui.

Páginas server-rendered, toda ação por form POST + redirect: funciona sem JS. As
duas exceções são os montadores (orçamento e visita), que são interativos por
natureza, e o deslizar do card na fila — e mesmo lá o card continua sendo um link
comum se o JS não carregar.
"""
from __future__ import annotations

import hashlib as _hashlib
import html as _html
import logging as _logging
import os as _os

from fastapi import APIRouter, Body, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from db.conexao import get_pool
from web import tema as _tema
from finance import cockpit as ck
from finance import cockpit_dono as cd

router = APIRouter()

_BASE = "/cockpit"
_PAPEIS_OK = ("vendedor", "gestor", "dono")


def esc(s) -> str:
    return _html.escape(str(s if s is not None else ""))


def _ini(s: str) -> str:
    s = (s or "?").strip()
    return (s[0].upper() if s else "?")


# ------------------------------------------------------------------ sessão
def _sessao(request: Request):
    """(conta_id, membro_id) do vendedor logado, ou None. Reusa a sessão do portal.

    Se a sessão caiu mas o aparelho tem o cookie "manter conectado", RECONSTRÓI a
    sessão aqui e segue — é o que faz o acesso ser indeterminado sem mexer no
    max_age global do SessionMiddleware (que vale pro painel do dono também).

    A reconstrução só escreve em `request.session`; o Starlette reassina o cookie na
    resposta sozinho. Não precisa de Response nenhum, e por isso cabe dentro de um
    guard que é chamado em toda rota."""
    mid = request.session.get("membro_id")
    cid = request.session.get("conta_id")
    papel = request.session.get("papel", "dono")
    if mid and cid and papel in _PAPEIS_OK:
        return cid, mid
    return _sessao_do_lembrete(request)


def _sessao_do_lembrete(request: Request):
    """Sessão vazia + cookie de "manter conectado" válido = entra de novo, calado.

    `lembrar_validar` relê o membro no banco, então desativar alguém na Equipe corta
    o acesso no request seguinte — a trava que uma sessão sem prazo exige."""
    token = request.cookies.get(ck.LEMBRETE_COOKIE)
    if not token:
        return None
    d = ck.lembrar_validar(get_pool(), token)
    if not d:
        return None
    request.session["conta_id"] = d["conta_id"]
    request.session["membro_id"] = d["membro_id"]
    request.session["papel"] = d["papel"]
    request.session["cockpit"] = True
    return d["conta_id"], d["membro_id"]


def _gerencia(request: Request):
    """(conta_id, membro_id) se dono/gestor — pra visão de equipe. NÃO exige membro_id:
    o dono logado no painel tem conta_id+papel mas não tem membro_id (é o titular), e
    a visão de equipe é toda por conta_id. Assim ele entra pela sessão do painel, sem
    precisar do login por link mágico. membro_id vem None pro dono, id pro gestor."""
    cid = request.session.get("conta_id")
    papel = request.session.get("papel", "dono")
    if not cid or papel not in ("dono", "gestor"):
        # o gestor também tem "manter conectado": sem isto ele voltaria a cair na
        # tela de entrada mesmo com o aparelho lembrado, já que várias rotas de
        # equipe checam `_gerencia` ANTES de `_sessao`.
        if _sessao_do_lembrete(request) is None:
            return None
        cid = request.session.get("conta_id")
        papel = request.session.get("papel", "dono")
        if not cid or papel not in ("dono", "gestor"):
            return None
    return cid, request.session.get("membro_id")


# ================================================================== marca
# Os tokens vêm de web/tema.py, que é a fonte única do painel inteiro — o mesmo
# bloco que o portal, o admin e a agenda usam. Aqui embaixo fica só o CSS de
# layout deste app (app shell, abas, folha de ações), que não existe em outro lugar.
_CSS = _tema.FONTES + """<style>""" + _tema.variaveis(com_base=False) + """
*{box-sizing:border-box}html,body{margin:0}
body{background:var(--bg);color:var(--text);font-family:var(--body);line-height:1.5;
  -webkit-font-smoothing:antialiased;overflow:hidden}
a{color:inherit;text-decoration:none}
b,strong{font-weight:600}
/* app shell: a página não rola; só o miolo (.scroll) rola, então header e abas
   ficam parados como em app nativo. */
/* padding-top: o _page pede viewport-fit=cover + status-bar-style black-translucent,
   que mandam desenhar POR BAIXO da barra de status de propósito (senão sobra tarja
   preta no app instalado). Quem faz isso tem que devolver o espaço, ou o título
   nasce embaixo do relógio. Fica aqui, e não no .hdr, porque cinco telas não têm
   cabeçalho — o login é uma delas, e é a primeira que o vendedor vê. */
.wrap{max-width:520px;margin:0 auto;height:100vh;height:100dvh;overflow:hidden;
  padding-top:env(safe-area-inset-top,0px);
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
/* mensagens do cliente sem resposta. Vermelho e à direita, longe do chip de
   estado: um diz DE QUEM é a vez, o outro diz QUANTO está esperando. Números
   redondos (min-width = height) e 9+ acima de nove, pra não esticar o card. */
.lead .pend{flex-shrink:0;min-width:20px;height:20px;padding:0 .32rem;border-radius:999px;
  background:var(--coral);color:#fff;font-family:var(--mono);font-size:.7rem;font-weight:700;
  display:inline-flex;align-items:center;justify-content:center;line-height:1}
.chip{font-size:.66rem;padding:.14rem .5rem;border-radius:999px;border:1px solid var(--line);
  color:var(--text-dim);flex-shrink:0;white-space:nowrap}
.chip.ia{color:var(--roxo);border-color:#3a2b52;background:#1a1226}
.chip.voce{color:var(--ambar);border-color:#5a4520;background:#241c0f}
.chip.neon{color:var(--neon);border-color:#1e4a3a;background:rgba(37,211,102,.10)}
.chip.err{color:var(--coral);border-color:#5a2b2b;background:#241313}

/* ---------- deslizar o card pra revelar ação ----------
   As ações ficam ATRÁS: o card da frente é opaco e escorrega pra esquerda por
   cima delas. Por isso `.front` precisa de fundo próprio — sem ele, as ações
   apareceriam por baixo do texto o tempo todo. */
.swipe{position:relative;overflow:hidden}
.swipe .actions{position:absolute;inset:0 0 0 auto;display:flex}
.swipe .act{width:84px;display:flex;flex-direction:column;align-items:center;justify-content:center;
  gap:.2rem;border:0;cursor:pointer;font-family:inherit;font-size:.7rem;font-weight:600}
.swipe .act .ic{width:19px;height:19px}
.act.assumir{background:var(--ambar);color:var(--sobre-verde)}
.act.devolver{background:var(--roxo);color:var(--sobre-verde)}
.act.ganho{background:var(--neon);color:var(--sobre-verde)}
/* `user-drag:none` não é firula: o card é um <a>, e arrastar um link dispara o
   drag-and-drop nativo do navegador, que engole os pointermove seguintes — o
   gesto morre no meio e o card volta sozinho. `touch-action:pan-y` deixa a
   rolagem vertical passar, que é o outro gesto que precisa continuar valendo. */
.swipe .front{position:relative;z-index:1;background:var(--bg);transition:transform .2s ease;
  touch-action:pan-y;-webkit-user-drag:none;user-select:none;-webkit-user-select:none}
.swipe .front.drag{transition:none}
.swipe .front:active{background:var(--bg-2)}
.dica-swipe{padding:.45rem 1.1rem .6rem;color:var(--text-faint);font-size:.72rem}
/* legenda curta embaixo de um botão de envio: pra quem toca saber ANTES pra onde vai */
.dica{color:var(--text-dim);font-size:.74rem;line-height:1.4;margin:.35rem 0 0;
  overflow-wrap:anywhere}
.dica b{color:var(--text)}
/* convite de notificação: nasce escondido e só aparece se o navegador ainda pode
   perguntar (nem concedido, nem negado) — quem já decidiu não vê nada */
.pushcard{display:none;margin:.8rem 1.1rem;border:1px solid #3a2b52;background:#160f22;
  border-radius:13px;padding:.85rem}
.pushcard.show{display:block}
.pushcard b{display:block;font-size:.92rem;margin-bottom:.2rem}
.pushcard p{margin:.1rem 0 .65rem;color:var(--text-dim);font-size:.8rem;line-height:1.45}
.pushcard .go{width:100%;background:var(--roxo);color:#1a0f2a;border:0;border-radius:10px;
  padding:.55rem .8rem;font-family:inherit;font-weight:700;font-size:.84rem;cursor:pointer}

/* ---------- fechar contrato (dois toques, porque mexe em dinheiro) ---------- */
.fechar{border:1px solid var(--line);background:var(--surface);border-radius:14px;padding:.2rem .9rem}
.fechar summary{list-style:none;cursor:pointer;padding:.65rem 0;font-family:var(--display);
  font-weight:700;font-size:.95rem;color:var(--neon);text-align:center}
.fechar summary::-webkit-details-marker{display:none}
.fechar[open]{border-color:#1e4a3a;background:rgba(37,211,102,.06)}
.fechar[open] summary{border-bottom:1px solid var(--line);margin-bottom:.7rem}
.fechar p{font-size:.82rem;color:var(--text-dim);margin:0 0 .6rem;line-height:1.5}
.fechar p b{color:var(--text)}
.fechar .aviso-p{color:var(--ambar);font-size:.78rem}

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
/* a fileira rola, mas sem barra de rolagem o chip cortado na borda parece defeito;
   o fade diz que tem mais coisa pro lado. */
.filt{display:flex;gap:.35rem;align-items:center;overflow-x:auto;padding:.45rem 1.1rem;
  scrollbar-width:none;
  -webkit-mask-image:linear-gradient(to right,#000 calc(100% - 18px),transparent);
  mask-image:linear-gradient(to right,#000 calc(100% - 18px),transparent)}
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
/* a mensagem que já está na tela mas ainda não voltou do servidor */
.bub.voando{opacity:.55}
.bub.voando .tick{display:block;font-family:var(--mono);font-size:.6rem;
  color:var(--text-faint);margin-top:.2rem}
.girando{display:block;width:15px;height:15px;margin:0 auto;border:2px solid rgba(0,0,0,.2);
  border-top-color:var(--ink);border-radius:50%;animation:gira .6s linear infinite}
@keyframes gira{to{transform:rotate(360deg)}}
/* ---------- o Z que se desenha ----------
   O logo é UM caminho contínuo, então ele pode se escrever na tela: dasharray +
   dashoffset é o próprio traço se revelando, sem biblioteca e sem imagem.
   Serve nos dois lugares — a cortina de abertura e o indicador das abas. */
.zdraw{fill:none;stroke:var(--neon);stroke-width:34;stroke-linecap:round;stroke-linejoin:round;
  stroke-dasharray:760;stroke-dashoffset:760}
@keyframes zescreve{to{stroke-dashoffset:0}}

/* a cortina da abertura: cobre a tela enquanto o Z se escreve, e sai */
.abertura{position:fixed;inset:0;z-index:60;background:var(--bg);display:flex;
  flex-direction:column;align-items:center;justify-content:center;gap:16px}
.abertura[hidden]{display:none}
.abertura svg{width:76px;height:76px;overflow:visible}
.abertura .zdraw{animation:zescreve .62s cubic-bezier(.65,.02,.3,1) forwards;
  filter:drop-shadow(0 0 8px var(--neon))}
.abertura .marca{font-family:var(--mono);font-size:.68rem;letter-spacing:.34em;
  text-transform:uppercase;color:var(--text-dim);padding-left:.34em;opacity:0;
  animation:zsurge .3s ease-out .36s forwards}
@keyframes zsurge{to{opacity:1}}
.abertura.saindo{animation:zsai .26s ease-in forwards}
@keyframes zsai{to{opacity:0}}

/* o mesmo traço, pequeno, no lugar do fio: some sozinho quando a tela chega e
   não segura nada — quem espera é a rede, não uma animação. */
.zprog{position:absolute;top:calc(env(safe-area-inset-top,0px) + 6px);left:50%;
  transform:translateX(-50%);width:26px;height:26px;z-index:9;opacity:0;
  pointer-events:none;transition:opacity .14s;overflow:visible}
.zprog.on{opacity:1}
.zprog.on .zdraw{animation:zescreve .75s cubic-bezier(.65,.02,.3,1) infinite}

@media (prefers-reduced-motion:reduce){
  .abertura .zdraw,.abertura .marca,.zprog.on .zdraw{animation:none}
  .abertura .zdraw{stroke-dashoffset:0}
  .abertura .marca{opacity:1}
}

/* fio de progresso: a única coisa na tela que diz "estou indo buscar". Fica no
   topo do .wrap, acima do cabeçalho, e some junto com o documento. */
/* o containing block do absoluto é a caixa de PADDING do .wrap, então top:0 cairia
   atrás da ilha dinâmica — o inset põe o fio logo abaixo da barra de status */
.prog{position:absolute;top:env(safe-area-inset-top,0px);left:0;height:2.5px;width:0;
  background:var(--neon);box-shadow:0 0 8px var(--neon);opacity:0;z-index:9;pointer-events:none}
.prog.on{opacity:1}
@media (prefers-reduced-motion:reduce){
  .prog{transition:none!important}
  .tabs .ic{transition:none}
  .girando{animation-duration:1.6s}
}
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
/* ficha/funil viram uma folha que sobe — antes isso empilhava ACIMA do chat
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
/* Número ÍMPAR de atalhos deixaria o último meia-largura com um buraco do lado —
   e ele passou a ser possível quando o atalho do WhatsApp saiu da conta que
   entrega tudo pelo Zaq. Em vez de inventar um quarto botão só pra emparelhar, o
   último ocupa a linha inteira. */
.grade a:last-child:nth-child(odd),.grade .off:last-child:nth-child(odd){grid-column:1/-1}
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
/* ficha do cliente: o vendedor preenche no meio da conversa, com uma mão. Duas
   colunas nos campos curtos pra não virar uma coluna interminável de campos. */
/* o <form> assume o lugar de item flex do shell: o miolo (.scroll) rola e o botão
   (.rodape-b) fica preso embaixo, sem depender do atributo form= no botão */
.telaform{flex:1;min-height:0;display:flex;flex-direction:column}
.fic{display:flex;flex-wrap:wrap;gap:.55rem}
.fic-c{flex:1 1 100%;display:flex;flex-direction:column;gap:.2rem}
.fic-c.meia{flex:1 1 calc(50% - .3rem);min-width:0}
.fic-c span{color:var(--text-dim);font-size:.76rem}
.fic-c input,.fic-c textarea{width:100%;min-width:0;background:var(--bg-2);
  border:1px solid var(--line);border-radius:10px;color:var(--text);
  padding:.55rem .7rem;font-family:inherit;font-size:.88rem;resize:none}
.fic-c input:focus,.fic-c textarea:focus{outline:none;border-color:var(--neon)}
.fic .btn{margin-top:.25rem}

/* ---------- abas de baixo ---------- */
.tabs{display:flex;flex-shrink:0;border-top:1px solid var(--line);background:rgba(10,15,12,.92);
  backdrop-filter:blur(12px);padding-bottom:env(safe-area-inset-bottom,0px);position:relative;z-index:2}
.tabs a{flex:1;display:flex;flex-direction:column;align-items:center;gap:2px;padding:.5rem 0 .45rem;
  color:var(--text-faint);font-size:.62rem;position:relative}
.tabs a.on{color:var(--neon)}
/* O toque tinha eco em card (.lead:active) e em nada mais: tocar numa aba não
   mudava um pixel até a página nova chegar, e em 4G isso é meio segundo de app
   que parece morto. Isto responde em 0 ms, sem rede e sem JS. */
.tabs a:active{background:var(--neon-fraco)}
.tabs a:active .ic{transform:scale(.88)}
.tabs .ic{transition:transform .09s ease-out}
/* o número em cima do ícone da aba. É o que dá a contagem no Android, onde a API
   de badge do ícone do app não existe — e serve de reforço no iOS. Fica absoluto
   pra não empurrar o ícone nem mudar a altura da barra. */
.tabs .tsel{position:absolute;top:.16rem;left:50%;margin-left:.28rem;min-width:15px;height:15px;
  padding:0 .22rem;border-radius:999px;background:var(--coral);color:#fff;font-family:var(--mono);
  font-size:.58rem;font-weight:700;line-height:15px;text-align:center;
  box-shadow:0 0 0 2px rgba(10,15,12,.92)}
.tabs a.on .ic{filter:drop-shadow(0 0 6px rgba(37,211,102,.5))}
.ic{width:21px;height:21px;stroke:currentColor;fill:none;stroke-width:1.7;
  stroke-linecap:round;stroke-linejoin:round}
.ic.p{width:17px;height:17px}

/* ---------- construtores (orçamento e visita) ---------- */
/* seletor Meus × Todos da agenda: dois links, um marcado. Links e não botões
   de propósito — o app inteiro é navegação por URL, e ?t= sobrevive a refresh. */
.seg-ag{display:flex;background:var(--surface);border:1px solid var(--line);
  border-radius:10px;padding:3px;gap:3px}
.seg-ag a{flex:1;text-align:center;padding:.45rem .3rem;border-radius:8px;
  font-size:.82rem;color:var(--text-dim);text-decoration:none}
.seg-ag a.on{background:var(--bg);color:var(--text);font-weight:600}
/* botão de novo lead: flutua acima das abas. Fixo e não no cabeçalho porque lá o
   `direita` já é o selo da conta — e o polegar alcança melhor embaixo à direita. */
.fab{position:fixed;right:16px;bottom:84px;z-index:40;width:52px;height:52px;
  border-radius:50%;background:var(--neon);color:#04150c;display:flex;align-items:center;
  justify-content:center;font-size:1.7rem;font-weight:400;line-height:1;text-decoration:none;
  box-shadow:0 6px 20px rgba(0,0,0,.45)}
.fab:active{transform:scale(.94)}
.toast{position:fixed;left:50%;bottom:88px;transform:translateX(-50%) translateY(12px);z-index:60;
  background:var(--surface);border:1px solid var(--line);border-radius:999px;padding:.5rem .95rem;
  font-size:.84rem;opacity:0;pointer-events:none;transition:opacity .2s,transform .2s;max-width:86%}
.toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
.secao{padding:.9rem 1.1rem 0}
.secao .rot{font-family:var(--mono);font-size:.66rem;letter-spacing:.16em;text-transform:uppercase;
  color:var(--text-faint);margin-bottom:.5rem}
/* item do catálogo: a linha inteira é o alvo do toque */
.srv{display:flex;align-items:center;gap:.75rem;padding:.7rem 1.1rem;border-bottom:1px solid var(--line);cursor:pointer}
.srv:active{background:var(--bg-2)}
.srv .ck{width:22px;height:22px;flex-shrink:0;border-radius:7px;border:1.5px solid var(--line);
  display:grid;place-items:center;font-size:.8rem;color:var(--sobre-verde)}
.srv.on .ck{background:var(--neon);border-color:var(--neon)}
.srv .m{flex:1;min-width:0}
.srv .m b{font-size:.92rem;font-weight:600}
.srv .m small{display:block;color:var(--text-faint);font-size:.76rem}
.srv .pr{flex-shrink:0;font-family:var(--mono);font-size:.8rem;color:var(--text-dim);text-align:right}
.srv.on .pr{color:var(--neon)}
.qtd{display:inline-flex;align-items:center;gap:.45rem;margin-top:.4rem}
.qtd button{width:24px;height:24px;border-radius:7px;border:1px solid var(--line);background:var(--surface);
  color:var(--text);font:inherit;font-size:.9rem;cursor:pointer;line-height:1}
.qtd span{font-family:var(--mono);font-size:.82rem;min-width:14px;text-align:center}
.avulso{display:flex;gap:.4rem;padding:0 1.1rem}
.avulso input{flex:1;min-width:0;background:var(--bg-2);border:1px solid var(--line);border-radius:10px;
  color:var(--text);padding:.55rem .7rem;font-family:inherit;font-size:.85rem}
.avulso input.v{flex:0 0 88px;font-family:var(--mono)}
.avulso button{flex-shrink:0;width:42px;border:1px solid var(--line);border-radius:10px;
  background:var(--surface);color:var(--neon);font-size:1.1rem;font-weight:700;cursor:pointer}
.vazio-cat{padding:1rem 1.1rem;color:var(--text-dim);font-size:.86rem;line-height:1.5}
/* o avulso agora leva o desconto também: a linha QUEBRA em duas em vez de
   espremer quatro controles numa faixa de 10 caracteres. */
.avl{display:flex;align-items:center;gap:.7rem;flex-wrap:wrap;margin:.45rem 1.1rem 0;
  padding:.5rem .7rem;border:1px solid var(--neon-borda);background:var(--neon-fraco);
  border-radius:10px}
.avl b{flex:1;min-width:0;font-size:.86rem;font-weight:500;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap}
.avl>span.vl{font-family:var(--mono);font-size:.8rem;color:var(--neon);flex-shrink:0;
  text-align:right}
.avl>span.vl s{display:block;color:var(--text-faint);font-size:.72rem;
  text-decoration:line-through}
.avl .dsc{flex:1 1 100%;margin-top:.1rem}
.avl button{flex-shrink:0;width:26px;height:26px;border-radius:8px;border:1px solid var(--line);
  background:var(--surface);color:var(--text-dim);font:inherit;font-size:1rem;line-height:1;cursor:pointer}
/* rodapé fixo do construtor: total à esquerda, ação à direita */
.rodape-b{flex-shrink:0;border-top:1px solid var(--line);background:var(--bg);padding:.8rem 1.1rem;
  padding-bottom:calc(.8rem + env(safe-area-inset-bottom,0px));position:relative;z-index:2}
.rodape-b .tot{display:flex;justify-content:space-between;align-items:baseline;gap:1rem;margin-bottom:.6rem;
  font-size:.85rem;color:var(--text-dim)}
.rodape-b .tot b{font-family:var(--mono);font-size:1rem;color:var(--neon)}
.rodape-b .tot b s{display:block;text-align:right;font-size:.74rem;color:var(--text-faint);
  text-decoration:line-through}
.btn[disabled]{opacity:.4;cursor:not-allowed;box-shadow:none}
/* ---------- gravador de voz (só canal QR; ver ck.pode_gravar_audio) ----------
   O vendedor da Prime manda 1 em cada 3 mensagens por áudio, com 32s de média.
   O botão fica ao lado do enviar, e enquanto grava ele TOMA a barra inteira —
   é o único jeito de o cancelar ser alcançável com uma mão. */
.composer .mic{flex:0 0 auto;width:40px;height:40px;border-radius:50%;border:0;
  background:var(--surface);color:var(--neon);display:grid;place-items:center;
  cursor:pointer;padding:0}
.composer .mic:active{background:var(--bg-2)}
.gravando{display:none;align-items:center;gap:.7rem;padding:.5rem .3rem}
.gravando.on{display:flex}
.composer.gravando-on input,.composer.gravando-on button[type=submit],
.composer.gravando-on .mic{display:none}
/* O microfone responde ao TOQUE, não ao getUserMedia: ele afunda na hora e a
   barra já aparece em "preparando". Sem isso a tela fica parada de 0,3 a 2s (a
   permissão e o hardware do celular demoram) e a pessoa acha que travou. */
.composer .mic:active{transform:scale(.92)}
.gravando .bolha{width:11px;height:11px;border-radius:50%;background:#E0654F;flex-shrink:0;
  opacity:.3;transition:opacity .15s}
.gravando.st-grav .bolha{animation:pisca 1.1s infinite;opacity:1}
@keyframes pisca{0%,100%{opacity:1}50%{opacity:.25}}
.gravando .rel{font-family:var(--mono);font-size:1rem;color:var(--text);min-width:44px;
  display:none}
.gravando.st-grav .rel,.gravando.st-env .rel{display:block}
.gravando.st-env .rel{color:var(--text-dim)}
/* medidor ao vivo: é ele que prova que o microfone está ouvindo. Um relógio
   correndo sozinho não distingue "gravando" de "gravando mudo". */
.gravando .nivel{display:none;flex:1;height:4px;border-radius:2px;background:var(--bg-2);
  overflow:hidden}
.gravando.st-grav .nivel{display:block}
.gravando .nivel i{display:block;height:100%;width:2%;background:var(--neon);
  border-radius:2px;transition:width .09s linear}
.gravando .dica{flex:1;font-size:.82rem;color:var(--text-dim)}
.gravando.st-grav .dica{display:none}
.gravando .cancela{background:none;border:0;color:var(--text-dim);font-size:.85rem;
  padding:.4rem .6rem;cursor:pointer;width:auto;flex-shrink:0}
.gravando.st-env .cancela,.gravando.st-env .manda{display:none}
.gravando .manda{flex:0 0 auto;width:40px;height:40px;border-radius:50%;border:0;
  background:var(--neon);color:var(--ink);display:grid;place-items:center;
  font-size:1.1rem;cursor:pointer;padding:0}
.gravando .manda:active{transform:scale(.92)}
.gravando.st-prep .manda{display:none}
@media (prefers-reduced-motion:reduce){
  .gravando.st-grav .bolha{animation:none}
  .gravando .nivel i{transition:none}
  .composer .mic:active,.gravando .manda:active{transform:none}
}
/* ---------- desconto (só nicho de serviço; ver ORC.desc) ----------
   O controle mora DENTRO da linha já marcada, na mesma altura do .qtd que já está
   ali — a linha não cresce duas vezes. A pílula %/R$ existe porque o vendedor
   negocia dos dois jeitos ("dou 5%" / "tiro 240"), e obrigar a converter de cabeça
   no meio da conversa com o cliente é onde o erro entra. */
.dsc{display:inline-flex;align-items:center;gap:.4rem;margin-top:.42rem}
.pil{display:inline-flex;background:var(--bg-2);border:1px solid var(--line);border-radius:8px;
  overflow:hidden;flex-shrink:0}
.pil button{border:0;background:none;font-family:var(--mono);font-size:.74rem;
  padding:.22rem .48rem;color:var(--text-faint);cursor:pointer;line-height:1.2}
.pil button.on{background:var(--neon);color:var(--ink);font-weight:700}
.cmp{font-family:var(--mono);font-size:.8rem;background:var(--bg-2);border:1px solid var(--line);
  border-radius:8px;padding:.2rem .42rem;width:60px;min-width:0;color:var(--text)}
.cmp:focus{outline:none;border-color:var(--neon)}
.dsc .tag{font-family:var(--mono);font-size:.7rem;color:var(--neon);white-space:nowrap}
.srv .pr s{display:block;color:var(--text-faint);font-size:.72rem;text-decoration:line-through}
.rodape-b .dlin{display:flex;justify-content:space-between;align-items:center;gap:.7rem;
  font-size:.82rem;color:var(--text-dim);padding:.1rem 0}
.rodape-b .dlin b{font-family:var(--mono);font-weight:400;color:var(--text)}
.rodape-b .dlin.desc span,.rodape-b .dlin.desc b{color:var(--neon)}
.rodape-b .dlin .dsc{margin-top:0}
/* tela de "pronto" que substitui o construtor depois de gerar */
.pronto{padding:2rem 1.1rem;text-align:center}
.pronto .big{font-size:2.4rem;margin-bottom:.6rem}
.pronto h3{font-family:var(--display);font-size:1.15rem;margin:0 0 .4rem}
.pronto p{color:var(--text-dim);font-size:.88rem;margin:0 0 1rem}
.pronto .ok{color:var(--neon);font-size:.84rem;margin:.5rem 0}
.evcard{border:1px solid var(--neon-borda);background:var(--neon-fraco);border-radius:14px;
  padding:1rem;text-align:left;margin:1rem 0}
.evcard .q{font-family:var(--mono);font-size:1rem;font-weight:700}
.evcard .l{color:var(--neon);font-size:.84rem;margin-top:.3rem}
.evcard .o{color:var(--text-faint);font-size:.76rem;margin-top:.5rem}
/* chips de escolha (dia, hora, duração, lembrete) */
.escolhas{display:flex;gap:.4rem;flex-wrap:wrap}
.esc{font-size:.82rem;padding:.4rem .75rem;border-radius:999px;border:1px solid var(--line);
  background:var(--surface);color:var(--text-dim);cursor:pointer;user-select:none}
.esc.on{border-color:var(--neon);background:var(--neon-fraco);color:var(--neon);font-weight:600}
.escolhas.horas{overflow-x:auto;flex-wrap:nowrap;scrollbar-width:none;padding-bottom:2px}
.escolhas.horas::-webkit-scrollbar{height:0}
.local{background:var(--surface);border:1px solid var(--line);border-radius:13px;padding:.75rem .85rem}
.local .nome{font-weight:600;font-size:.9rem}
.local .end{color:var(--text-dim);font-size:.82rem;margin-top:.15rem}
.local a{display:inline-block;color:var(--neon);font-size:.8rem;margin-top:.4rem}
.local input{width:100%;margin-top:.55rem;background:var(--bg-2);border:1px solid var(--line);
  border-radius:9px;color:var(--text);padding:.5rem .65rem;font-family:inherit;font-size:.85rem}
.linha-tgl{display:flex;align-items:center;gap:1rem;background:var(--surface);border:1px solid var(--line);
  border-radius:13px;padding:.75rem .85rem}
.linha-tgl .t{flex:1;font-size:.88rem}
.linha-tgl .t small{display:block;color:var(--text-faint);font-size:.76rem}
.tgl{flex-shrink:0;border:1px solid var(--neon-borda);background:var(--neon);color:var(--sobre-verde);
  border-radius:999px;padding:.35rem .8rem;font-size:.8rem;font-weight:700;cursor:pointer}
.tgl.off{background:transparent;border-color:var(--line);color:var(--text-dim)}
.msg-previa{margin-top:.6rem;background:var(--neon-fraco);border:1px solid var(--neon-borda);
  border-radius:11px;padding:.65rem .75rem;font-size:.8rem;color:var(--text-dim);white-space:pre-line}

/* ---------- recado da última ação ---------- */
.flash{margin:.7rem 1.1rem 0;padding:.6rem .8rem;border-radius:12px;font-size:.84rem}
.flash.ok{background:rgba(37,211,102,.12);border:1px solid #1e4a3a;color:var(--neon)}
.flash.err{background:#241313;border:1px solid #5a2b2b;color:var(--coral)}

/* ---------- entrar (senha + link) ---------- */
.login{flex:1;display:flex;flex-direction:column;justify-content:center;padding:2.4rem 1.6rem;
  gap:.4rem;text-align:center}
.login .marca{font-family:var(--display);font-size:2rem;font-weight:800;letter-spacing:-.03em;
  color:var(--neon);margin-bottom:.3rem}
.login h2{font-family:var(--display);font-size:1.3rem;margin:.2rem 0 0;letter-spacing:-.02em}
.login p{color:var(--text-dim);font-size:.9rem;margin:.35rem 0 1rem;line-height:1.5}
.login input{background:var(--bg-2);border:1px solid var(--line);border-radius:12px;
  color:var(--text);padding:.85rem .9rem;font-family:inherit;font-size:.95rem;width:100%}
.login .go{display:block;width:100%;background:var(--neon);color:var(--sobre-verde);border:0;
  border-radius:12px;padding:.85rem;font-family:inherit;font-weight:700;font-size:.95rem;
  margin-top:.7rem;cursor:pointer;text-decoration:none}
.login small{color:var(--text-faint);font-size:.76rem;margin-top:.9rem;line-height:1.5}
/* segunda ação da tela: mesma forma do botão principal, sem o peso do verde cheio —
   entrar por link é a saída, não o caminho de todo dia. */
.login .go2{display:block;width:100%;background:transparent;color:var(--neon);
  border:1px solid var(--neon-borda);border-radius:12px;padding:.8rem;font-family:inherit;
  font-weight:600;font-size:.9rem;margin-top:.55rem;cursor:pointer;text-decoration:none}
/* dois <form> empilhados não podem virar dois blocos separados por margem: a tela é
   uma coluna só, e o gap do .login já dá o respiro. */
.login form{margin:0;width:100%}
.login .chk{display:flex;align-items:center;gap:.5rem;justify-content:center;
  color:var(--text-dim);font-size:.85rem;margin-top:.7rem;cursor:pointer}
.login .chk input{width:auto;accent-color:var(--neon);margin:0}
.login .erro{background:#2A1613;border:1px solid #5A2A22;color:#E8705C;border-radius:10px;
  padding:.6rem .75rem;font-size:.82rem;margin-bottom:.4rem}
.login .nota{background:var(--neon-fundo);border:1px solid var(--neon-borda);color:var(--neon);
  border-radius:10px;padding:.6rem .75rem;font-size:.82rem;margin-bottom:.4rem}

.fonte{margin:.2rem 1.1rem 1rem;font-size:.7rem;color:var(--text-faint);line-height:1.45}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
/* O Safari do iPhone dá zoom na página quando o dedo entra num campo com menos
   de 16px — e não desfaz ao sair. Era isso que entortava o app inteiro: bastava
   tocar em "Responder…" e a conversa passava a sair cortada pela direita o resto
   da sessão. Os campos daqui são todos menores (.72rem a .95rem), então a regra
   fica num lugar só, valendo pra composer, login, orçamento e visita. Só em tela
   de toque: no desktop o tamanho desenhado continua igual. O !important é
   necessário: media query não muda especificidade, e as regras de campo daqui são
   todas de classe (.composer input, .login input), que ganhariam de um seletor de
   elemento. Mesma licença que a linha do prefers-reduced-motion acima. */
@media (pointer:coarse){input,select,textarea{font-size:16px!important}}
</style>"""

# Ícones no traço do set que o painel já usa (web/portal.py) — stroke 1.7, viewBox 24.
# A versão anterior usava emoji no menu (📊 🏆 📋 ⚡), que muda de desenho a cada sistema.
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
    # os três do deslizar: puxar pra mim, devolver pro agente, marcar ganho
    '<symbol id="i-assumir" viewBox="0 0 24 24"><path d="M12 3v11M8 10.5l4 4 4-4"/>'
    '<path d="M4 16v3a2 2 0 002 2h12a2 2 0 002-2v-3"/></symbol>'
    '<symbol id="i-bot" viewBox="0 0 24 24"><rect x="4" y="8" width="16" height="11" rx="3"/>'
    '<path d="M12 4v4M9 22h6M9 13v1.5M15 13v1.5"/></symbol>'
    '<symbol id="i-check" viewBox="0 0 24 24"><path d="M4.5 12.5l5 5 10-11"/></symbol>'
    '</defs></svg>')


def _ic(nome: str, cls: str = "ic") -> str:
    return f'<svg class="{cls}"><use href="#i-{nome}"/></svg>'


# A folha saiu de dentro do HTML. Eram 33 KB — 85% do documento — reenviados a
# cada navegação, e o app é form + redirect: o vendedor troca de tela o tempo
# todo. Agora o HTML vai com ~6 KB e a folha vem do disco depois da 1ª vez (ver
# o cache-primeiro no sw.js).
#
# A versão sai do CONTEÚDO: mudou o CSS, muda a URL, e o navegador busca a nova
# sem ninguém precisar lembrar de virar um número à mão.
_CSS_TEXTO = _CSS[len(_tema.FONTES):].replace("<style>", "", 1).rsplit("</style>", 1)[0]
_CSS_VER = _hashlib.sha1(_CSS_TEXTO.encode()).hexdigest()[:10]


#: (largura, altura, densidade) dos iPhones em uso. O iOS casa o splash por media
#: query de tamanho CSS + densidade — não por modelo — então é isso que importa.
#: Aparelho fora da lista simplesmente não recebe splash e volta ao fundo liso,
#: que é o comportamento de hoje: nada piora.
_SPLASH = [(375, 812, 3), (390, 844, 3), (393, 852, 3), (402, 874, 3),
           (414, 896, 2), (428, 926, 3), (430, 932, 3), (440, 956, 3)]
_SPLASH_DIR = _os.path.join(_os.path.dirname(__file__), "estatico", "splash")
_splash_cache: dict[str, bytes] = {}


def _splash_tags() -> str:
    """As tags que o iPhone lê pra saber o que mostrar enquanto o app abre.

    O manifest já tem `background_color`, e o Android usa isso com ícone e nome —
    mas o iOS ignora o manifest e quer `apple-touch-startup-image`, uma imagem em
    tamanho EXATO por aparelho. Sem elas, o que aparecia era o fundo da marca
    vazio: a "tela preta" que o vendedor via já era `#0A0F0C`, só que sem nada."""
    out = []
    for w, h, d in _SPLASH:
        media = (f"(device-width:{w}px) and (device-height:{h}px) and "
                 f"(-webkit-device-pixel-ratio:{d}) and (orientation:portrait)")
        out.append(f"<link rel='apple-touch-startup-image' media='{media}' "
                   f"href='{_BASE}/splash/{w}x{h}@{d}.png'>")
    return "".join(out)


@router.get("/cockpit/splash/{nome}.png", include_in_schema=False)
def cockpit_splash(nome: str):
    """Nome validado contra a lista, não montado por concatenação: `{nome}.png`
    com join direto deixaria `../../` sair da pasta."""
    if nome not in {f"{w}x{h}@{d}" for w, h, d in _SPLASH}:
        return Response(status_code=404)
    if nome not in _splash_cache:
        try:
            with open(_os.path.join(_SPLASH_DIR, f"{nome}.png"), "rb") as fh:
                _splash_cache[nome] = fh.read()
        except OSError:
            return Response(status_code=404)
    return Response(_splash_cache[nome], media_type="image/png",
                    headers={"Cache-Control": "public, max-age=2592000"})


@router.get("/cockpit/app.css", include_in_schema=False)
def cockpit_css():
    return Response(_CSS_TEXTO, media_type="text/css",
                    # a URL carrega o hash do conteúdo, então o arquivo é imutável
                    headers={"Cache-Control": "public, max-age=31536000, immutable"})


# ================================================================== helpers
def _page(title: str, corpo: str) -> HTMLResponse:
    """O documento. Leva manifest + service worker porque este app é pra instalar
    na tela inicial do celular do vendedor — é o caminho normal de uso dele."""
    return HTMLResponse(
        "<!doctype html><html lang=pt-br><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1,viewport-fit=cover"
        ",interactive-widget=resizes-content'>"
        "<meta name=theme-color content='#0A0F0C'><meta name=robots content=noindex>"
        "<link rel=manifest href='/cockpit/manifest.webmanifest'>"
        "<link rel='apple-touch-icon' href='/cockpit/icon.svg'>"
        "<meta name='apple-mobile-web-app-capable' content=yes>"
        "<meta name='apple-mobile-web-app-status-bar-style' content='black-translucent'>"
        + _splash_tags() +
        # O @font-face fica INLINE de propósito, enquanto a folha grande sai pra
        # arquivo: se ele morasse no .css externo, o navegador só descobriria as
        # fontes depois de baixar a folha — uma requisição esperando a outra.
        # Assim a fonte começa a vir no primeiro quadro.
        f"<title>{esc(title)} · Zaq</title>{_tema.FONTES}"
        f"<link rel=stylesheet href='{_BASE}/app.css?v={_CSS_VER}'>"
        "</head><body>" + _ICONES + _ABERTURA_HTML +
        f"<div class=wrap><div class=glow></div>{_ZPROG}{corpo}</div>"
        "<script>if('serviceWorker' in navigator)"
        "navigator.serviceWorker.register('/cockpit/sw.js',{scope:'/cockpit'})"
        ".catch(function(){});</script>"
        + _ESPERA_JS +
        "</body></html>")


#: O traço do logo, sozinho — o mesmo `d` do _ICON_SVG. Some a moldura: na cortina
#: o fundo já é o da marca, e no indicador ela viraria um quadrado no meio da tela.
_Z_PATH = "<path class=zdraw d='M170 150 h150 L190 362 h150'/>"

#: o Z pequeno que substitui o fio de progresso ao trocar de aba
_ZPROG = f"<svg class=zprog id=zprog viewBox='0 0 512 512' aria-hidden=true>{_Z_PATH}</svg>"

# A cortina de abertura. Fica no HTML, não no splash do iOS, por dois motivos:
# o `apple-touch-startup-image` é PNG e não anima, e o iPhone guarda as imagens
# de um app já instalado com teimosia — trocá-las não chega em quem já instalou.
# Aqui é HTML nosso: aparece para todo mundo, na hora.
#
# Nasce ESCONDIDA e o script decide. Sem JS, ninguém vê cortina nenhuma e o app
# abre direto, que é o comportamento de sempre.
_ABERTURA_HTML = (
    "<div class=abertura id=abertura hidden>"
    f"<svg viewBox='0 0 512 512' aria-hidden=true>{_Z_PATH}</svg>"
    "<span class=marca>Zaq</span></div>"
    # roda DURANTE o parse, antes do primeiro quadro: decidir depois faria a fila
    # piscar antes da cortina cobrir.
    "<script>(function(){try{"
    # uma vez por SESSÃO, não por navegação. Trocar de aba é navegação inteira
    # neste app — sem esta trava, o Z tomaria a tela a cada toque na barra.
    "if(sessionStorage.getItem('zaqAberto'))return;"
    "sessionStorage.setItem('zaqAberto','1');"
    "var a=document.getElementById('abertura');if(!a)return;a.hidden=false;"
    "setTimeout(function(){a.className='abertura saindo';"
    "setTimeout(function(){a.hidden=true;a.className='abertura';},260);},700);"
    "}catch(e){}})();</script>")


# O app é form + redirect: todo toque numa aba, num card ou no enviar é uma
# navegação inteira, com ida ao servidor. Isso não vai mudar — é o que mantém o
# app simples e funcionando sem JS. O que estava errado é que ele não CONTAVA
# nada nesse meio tempo, e silêncio de sistema o usuário lê como falha.
#
# Nada aqui altera o que é enviado nem para onde: sem JS, tudo funciona como
# antes. É só o app parando de esconder que está trabalhando.
_ESPERA_JS = """<script>(function(){
  // O Z se desenhando no lugar do fio: mesma função, cara da marca. Ele fica em
  // laço enquanto a tela nova não chega e some junto com o documento — nunca
  // segura nada, porque quem faz esperar é a rede, não a animação.
  var prog=document.getElementById('zprog');
  function corre(){ if(prog)prog.classList.add('on'); }
  function para(){ if(prog)prog.classList.remove('on'); }

  document.addEventListener('click',function(e){
    var a=e.target.closest&&e.target.closest('a');
    if(!a||e.defaultPrevented||e.metaKey||e.ctrlKey||a.target==='_blank')return;
    var h=a.getAttribute('href')||'';
    // só navegação DENTRO do app: âncora (#acoes, #fechar), tel: e wa.me ficam de fora
    if(h.charAt(0)!=='/'||h.indexOf('/cockpit')!==0)return;
    var abas=a.parentNode&&a.parentNode.classList&&a.parentNode.classList.contains('tabs');
    if(abas){
      // a aba destino acende AGORA, antes de qualquer rede
      var irmaos=a.parentNode.querySelectorAll('a');
      for(var i=0;i<irmaos.length;i++){irmaos[i].classList.remove('on');}
      a.classList.add('on');
    }
    corre();
  },true);

  // voltar pelo histórico devolve a página do cache com a barra congelada no meio
  window.addEventListener('pageshow',para);

  // ---- o enviar ----
  // A bolha entra na hora e o campo esvazia. O campo CHEIO depois de tocar em
  // enviar é o sinal universal de "não foi" — e aqui a dúvida não é sobre uma
  // tela, é sobre o cliente ter recebido.
  var f=document.querySelector('form.composer');
  if(f) f.addEventListener('submit',function(){
    var campo=f.querySelector('input[name=texto]');
    if(!campo)return;
    var txt=(campo.value||'').trim();
    if(!txt)return;
    // o valor migra pra um hidden ANTES de esvaziar o visível: limpar o campo
    // que carrega o name mandaria texto vazio pro servidor.
    var hid=document.createElement('input');
    hid.type='hidden';hid.name='texto';hid.value=txt;
    f.appendChild(hid);campo.removeAttribute('name');campo.value='';campo.blur();
    var b=f.querySelector('button');
    if(b){b.disabled=true;b.innerHTML='<i class=girando></i>';}
    var chat=document.querySelector('.chat');
    if(chat){
      var d=document.createElement('div');
      d.className='bub out voando';
      d.textContent=txt;
      var s=document.createElement('span');s.className='tick';s.textContent='enviando…';
      d.appendChild(s);chat.appendChild(d);chat.scrollTop=chat.scrollHeight;
    }
    corre();
  });
})();</script>"""


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


def _hdr(titulo: str, sub: str = "", *, voltar: str = "", direita: str = "",
         inicial: str = "", href_inicial: str = "") -> str:
    esq = (f"<a class=bk href='{esc(voltar)}'>{_ic('volta')}</a>" if voltar else "")
    if not esq and inicial:
        alvo = href_inicial or f"{_BASE}/perfil"
        esq = f"<a class=av href='{esc(alvo)}'>{esc(inicial)}</a>"
    return (f"<div class=hdr>{esq}<div class=tt><b>{esc(titulo)}</b>"
            + (f"<small>{esc(sub)}</small>" if sub else "") + f"</div>{direita}</div>")


def _abas(itens, ativo: str, selos: dict | None = None) -> str:
    """Barra de abas. É o que faltava no app do vendedor — ele tinha uma tela só."""
    selos = selos or {}
    out = []
    for chave, icone, rotulo, href in itens:
        on = " class=on" if chave == ativo else ""
        n = int(selos.get(chave) or 0)
        selo = (f"<span class=tsel aria-label='{n} sem resposta'>"
                f"{n if n < 10 else '9+'}</span>") if n else ""
        out.append(f"<a{on} href='{esc(href)}'>{_ic(icone)}{selo}"
                   f"<span>{esc(rotulo)}</span></a>")
    return "<div class=tabs>" + "".join(out) + "</div>"


def _abas_vend(ativo: str, pend: int = 0) -> str:
    return _abas([("fila", "fila", "Fila", _BASE),
                  ("agenda", "agenda", "Agenda", f"{_BASE}/agenda"),
                  ("orcamentos", "orc", "Propostas", f"{_BASE}/orcamentos"),
                  ("resultado", "resultado", "Resultado", f"{_BASE}/resultado"),
                  ("perfil", "perfil", "Perfil", f"{_BASE}/perfil")],
                 ativo, {"fila": pend})


def _pend_vend(conta_id: int, membro_id: int) -> int:
    """Total sem resposta pro selo da aba Fila.

    A aba aparece em TODAS as telas do vendedor, então o número vai junto: ele está
    na Agenda e vê que a fila tem 3 esperando, sem precisar entrar. É também o que
    dá o número no ANDROID, onde `setAppBadge` não existe — lá o ícone ganha, no
    máximo, um pontinho sem contagem.

    Best-effort de propósito: barra de abas não pode derrubar a tela se a consulta
    falhar. Sem o número, a aba ainda é uma aba."""
    try:
        return ck.total_pendentes(get_pool(), conta_id, membro_id)
    except Exception:  # noqa: BLE001
        return 0


def _abas_dono(ativo: str) -> str:
    # O Perfil já saiu daqui uma vez, virando só o avatar do topo, porque "com seis
    # abas os rótulos não cabem numa tela de 390px". A preocupação com o espaço era
    # justa; a consequência não foi vista: o avatar é as INICIAIS DA EMPRESA num
    # círculo sem rótulo, e ele é a única porta pro Sair. Quem entrava no app de
    # gestão não achava como sair — foi o que aconteceu na prática.
    #
    # Medido antes de trazer de volta, em 390px: o maior rótulo ("Propostas") ocupa
    # 48px numa faixa de 65, todos em uma linha, e a barra fica com os mesmos 54px
    # de altura de quando tinha cinco. Cabe — não precisou encurtar nada.
    # Sete abas, medido de novo em 390px: ~55px por aba, e o maior rótulo
    # ("Propostas", .58rem) ocupa ~46px. Continua numa linha só.
    return _abas([("visao", "visao", "Visão", _BASE),
                  ("agenda", "agenda", "Agenda", f"{_BASE}/agenda"),
                  ("placar", "placar", "Placar", f"{_BASE}/equipe/placar"),
                  ("leads", "leads", "Leads", f"{_BASE}/equipe/leads"),
                  ("orcamentos", "orc", "Propostas", f"{_BASE}/orcamentos"),
                  ("ativ", "ativ", "Atividade", f"{_BASE}/equipe/atividade"),
                  ("perfil", "perfil", "Perfil", f"{_BASE}/perfil")], ativo)


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
@router.get("/cockpit", response_class=HTMLResponse)
def cockpit_inicio(request: Request, meus: str = ""):
    """Bifurca como sempre foi: dono/gestor cai na visão de equipe, vendedor na fila.

    `?meus=1` é a saída pro gestor que TAMBÉM vende: na versão anterior ele nunca chegava na
    própria caixa, porque `_gerencia` é testado antes de `_sessao`.
    """
    g = _gerencia(request)
    if g and not (meus and g[1]):
        return _dono_visao(request, g[0])
    sess = _sessao(request)
    if not sess:
        return RedirectResponse("/cockpit/login", status_code=303)
    return _fila(request, sess[0], sess[1], gestor=bool(g))


# Deslizar o card. É a única tela do app com gesto — o resto é form + redirect —
# e existe porque assumir/devolver na fila é o que o vendedor mais repete no dia.
# Os POSTs respondem JSON quando vem o header `x-cockpit: 1`, pra ação gravar sem
# tirar ele da lista; sem JS o card continua sendo um link comum pro lead.
_FILA_JS = r"""
<script>
(function(){
  var B=window.CKBASE||"/cockpit";
  function $(id){return document.getElementById(id);}
  function toast(m){var t=$("toast");if(!t)return;t.textContent=m;t.classList.add("show");
    clearTimeout(t._t);t._t=setTimeout(function(){t.classList.remove("show");},2200);}
  var IC={assumir:'<svg class="ic"><use href="#i-assumir"/></svg>',
          bot:'<svg class="ic"><use href="#i-bot"/></svg>'};
  function acoesHTML(ia){
    var p=ia?'<button class="act assumir" data-a="assumir">'+IC.assumir+'Assumir</button>'
            :'<button class="act devolver" data-a="devolver">'+IC.bot+'Devolver</button>';
    return '<div class="actions">'+p+'<button class="act ganho" data-a="ganho">'
      +'<svg class="ic"><use href="#i-check"/></svg>Ganho</button></div>';
  }
  var aberto=null;
  function fecha(f){if(!f)return;f.classList.remove("open");f.style.transform="translateX(0)";
    if(aberto===f)aberto=null;}
  function larg(row){var a=row.querySelector(".actions");return a?a.offsetWidth:168;}

  function ligar(row){
    var front=row.querySelector(".front"), id=row.getAttribute("data-id");
    var sx=0,sy=0,base=0,arrastando=false,decidiu=false,horiz=false,mexeu=false;
    front.addEventListener("pointerdown",function(e){
      if(aberto&&aberto!==front)fecha(aberto);
      arrastando=true;decidiu=false;horiz=false;mexeu=false;sx=e.clientX;sy=e.clientY;
      base=front.classList.contains("open")?-larg(row):0;
      front.classList.add("drag");try{front.setPointerCapture(e.pointerId);}catch(_){}
    });
    front.addEventListener("pointermove",function(e){
      if(!arrastando)return;
      var mx=e.clientX-sx,my=e.clientY-sy;
      // só decide o eixo depois de 6px: senão o gesto rouba a rolagem vertical
      if(!decidiu&&(Math.abs(mx)>6||Math.abs(my)>6)){decidiu=true;horiz=Math.abs(mx)>Math.abs(my);}
      if(!horiz)return;
      e.preventDefault();mexeu=true;
      var w=larg(row);
      front.style.transform="translateX("+Math.max(-w-20,Math.min(0,base+mx))+"px)";
    });
    function solta(){
      if(!arrastando)return;
      arrastando=false;front.classList.remove("drag");
      var m=front.style.transform.match(/-?\d+\.?\d*/),cur=m?parseFloat(m[0]):0,w=larg(row);
      if(cur<-w/2){front.classList.add("open");front.style.transform="translateX("+(-w)+"px)";aberto=front;}
      else fecha(front);
    }
    front.addEventListener("pointerup",solta);
    front.addEventListener("pointercancel",solta);
    front.addEventListener("click",function(e){
      // O arrasto termina em pointerup, e o navegador dispara um CLICK logo
      // depois. Sem esta primeira linha esse click fecharia na hora o card que o
      // arrasto acabou de abrir — some antes de dar pra ler as ações.
      if(mexeu){e.preventDefault();mexeu=false;return;}
      // toque de verdade num card aberto: fecha em vez de abrir o lead
      if(front.classList.contains("open")){e.preventDefault();fecha(front);}
    });
    liga_acoes(row,front,id);
  }
  function liga_acoes(row,front,id){
    row.querySelectorAll(".act").forEach(function(b){
      b.addEventListener("click",function(ev){
        ev.stopPropagation();agir(row,front,id,b.getAttribute("data-a"));});
    });
  }
  function agir(row,front,id,a){
    var url=B+"/lead/"+id+(a==="ganho"?"/fechar":a==="devolver"?"/devolver":"/assumir");
    var opt={method:"POST",headers:{"x-cockpit":"1"}};
    if(a==="ganho"){opt.headers["Content-Type"]="application/x-www-form-urlencoded";
      opt.body="tipo=ganho";}
    fetch(url,opt).then(function(r){return r.json();}).then(function(j){
      if(!j||!j.ok){toast((j&&j.erro)||"Não deu certo");fecha(front);return;}
      if(a==="ganho"){
        row.style.height=row.offsetHeight+"px";row.style.overflow="hidden";
        row.style.transition="height .25s,opacity .25s";
        requestAnimationFrame(function(){row.style.height="0";row.style.opacity="0";});
        setTimeout(function(){row.remove();},260);
        toast("Marcado como Ganho 🎉");return;
      }
      var ia=(a!=="assumir");   // assumir → sai da IA; devolver → volta pra IA
      var tmp=document.createElement("div");tmp.innerHTML=acoesHTML(ia);
      row.querySelector(".actions").replaceWith(tmp.firstChild);
      var chip=front.querySelector(".chip");
      if(chip){chip.className="chip "+(ia?"ia":"voce");chip.textContent=ia?"IA":"sua vez";}
      liga_acoes(row,front,id);
      fecha(front);
      toast(a==="assumir"?"É a sua vez — agente desligado":"Devolvido pro agente");
    }).catch(function(){toast("Falha de conexão");fecha(front);});
  }
  document.querySelectorAll(".swipe").forEach(ligar);

  // ---- push ----
  // O botão do Perfil só marca a preferência no banco. Quem realmente assina é
  // o navegador, e só aqui: `Notification.requestPermission()` exige um gesto do
  // usuário, então precisa de um botão de verdade na tela.
  function urlB64(s){
    var p="=".repeat((4-s.length%4)%4);
    var b=atob((s+p).replace(/-/g,"+").replace(/_/g,"/"));
    var a=new Uint8Array(b.length);
    for(var i=0;i<b.length;i++)a[i]=b.charCodeAt(i);
    return a;
  }
  function assinar(){
    if(!("serviceWorker" in navigator)||!("PushManager" in window)||!window.CKVAPID)
      return Promise.resolve(false);
    return navigator.serviceWorker.ready.then(function(reg){
      return reg.pushManager.getSubscription().then(function(s){
        return s||reg.pushManager.subscribe(
          {userVisibleOnly:true,applicationServerKey:urlB64(window.CKVAPID)});
      });
    }).then(function(sub){
      return fetch(B+"/push/assinar",{method:"POST",
        headers:{"Content-Type":"application/json"},body:JSON.stringify(sub)});
    }).then(function(){return true;}).catch(function(){return false;});
  }
  var card=$("pushcard");
  if(card&&("Notification" in window)&&window.CKVAPID){
    if(Notification.permission==="granted")assinar();
    else if(Notification.permission!=="denied"){
      card.classList.add("show");
      $("pushbtn").addEventListener("click",function(){
        Notification.requestPermission().then(function(p){
          if(p==="granted")assinar().then(function(){toast("Notificações ligadas");});
          else toast("Dá pra ligar depois, no navegador");
          card.classList.remove("show");
        });
      });
    }
  }
})();
</script>"""


def _acoes_card(ia: bool) -> str:
    """As duas ações que ficam atrás do card. A primeira alterna: quem está com a IA
    o vendedor ASSUME; quem já é dele ele DEVOLVE."""
    p = (f"<button class='act assumir' data-a=assumir>{_ic('assumir')}Assumir</button>" if ia
         else f"<button class='act devolver' data-a=devolver>{_ic('bot')}Devolver</button>")
    return (f"<div class=actions>{p}"
            f"<button class='act ganho' data-a=ganho>{_ic('check')}Ganho</button></div>")


def _fila(request: Request, conta_id: int, membro_id: int, *, gestor: bool = False) -> HTMLResponse:
    pool = get_pool()
    leads = ck.leads_do_vendedor(pool, conta_id, membro_id)
    p = ck.perfil(pool, conta_id, membro_id)
    vez = sum(1 for l in leads if not l["ia"])

    # a fila já tem os leads em mão: soma daqui, sem uma consulta a mais só pra aba
    total_pend = sum(int(l.get("pend") or 0) for l in leads)

    cartoes = []
    for l in leads:
        chip = ("<span class='chip ia'>IA</span>" if l["ia"]
                else "<span class='chip voce'>sua vez</span>")
        # Quantas o cliente mandou e ninguém respondeu. O push é aviso que passa —
        # chega uma vez, e se o vendedor estiver dirigindo ou com o foco ligado,
        # passou. A bolinha fica até a conversa ser respondida, que é o que faz o
        # lead esquecido continuar visível no dia seguinte.
        pend = int(l.get("pend") or 0)
        selo = (f"<span class=pend aria-label='{pend} sem resposta'>"
                f"{pend if pend < 10 else '9+'}</span>") if pend else ""
        # sem JS o card ainda é um link normal pro lead — o deslizar só acrescenta
        cartoes.append(
            f"<div class=swipe data-id='{l['id']}'>{_acoes_card(bool(l['ia']))}"
            f"<a class='lead front' draggable=false href='{_BASE}/lead/{l['id']}'>"
            f"<span class=dot style='background:{_TEMP.get(l['temperatura'], 'var(--azul)')}'></span>"
            f"<span class=mid><span class=top><span class=emp>{esc(l['empresa'])}</span>{chip}</span>"
            f"<span class=snip>{esc(l['snip'])}</span></span>{selo}</a></div>")
    lista = "".join(cartoes) or (
        "<div class=vazio><div class=big>◎</div><b>Fila zerada</b>"
        "Nenhum lead aberto agora. Quando cair um novo no rodízio, você é avisado.</div>")
    dica = ("<div class=dica-swipe>Arraste um card pra esquerda pra assumir, devolver "
            "ou marcar ganho.</div>") if cartoes else ""

    # push só aparece se as chaves VAPID estão no ambiente — sem elas, oferecer
    # notificação seria prometer o que o servidor não sabe entregar
    import json as _json
    from finance import webpush
    vapid = webpush.chave_publica()
    pushcard = ("<div class=pushcard id=pushcard><b>Ative as notificações</b>"
                "<p>Receba o aviso no celular assim que um lead cair pra você — "
                "mesmo com o app fechado.</p>"
                "<button class=go id=pushbtn type=button>Ativar notificações</button></div>"
                ) if vapid else ""
    vapid_js = f"<script>window.CKVAPID={_json.dumps(vapid)};</script>" if vapid else ""

    volta = ("<div class=bloco style='margin-top:.9rem'>"
             f"<a class='btn ghost' href='{_BASE}'>Ver a visão da equipe</a></div>") if gestor else ""
    corpo = (_hdr("Meus leads", f"{len(leads)} abertos · {vez} sua vez",
                  inicial=_ini(p["nome"]), direita=_selo(conta_id))
             + _flash(request)
             + f"<div class=scroll>{pushcard}{lista}{dica}{volta}</div>"
             + (f"<a class=fab href='{_BASE}/lead/novo' aria-label='Novo lead'>+</a>"
                if not gestor else "")
             + "<div class=toast id=toast></div>"
             + _abas_vend("fila", total_pend)
             + f'<script>window.CKBASE="{_BASE}";</script>' + vapid_js + _FILA_JS
             + _sinal_js(ck.sinal_fila(pool, conta_id, membro_id))
             + _badge_js(total_pend))
    return _page("Meus leads", corpo)


def _badge_js(total: int) -> str:
    """Acerta a bolinha do ícone toda vez que a fila abre.

    O push já marca o ícone com o app fechado (ver o sw.js), mas ele só roda quando
    chega mensagem. Isto é o outro lado: fechou tudo respondendo, a bolinha some;
    respondeu pelo WhatsApp direto e voltou ao app, o número se corrige sozinho.
    Sem isto a bolinha grudaria num valor velho até o próximo push."""
    return ("<script>(function(){var n=" + str(int(total)) + ";"
            "if(!navigator.setAppBadge)return;"        # navegador sem a API: nem tenta
            "try{(n>0?navigator.setAppBadge(n):navigator.clearAppBadge())"
            ".catch(function(){});}catch(_){}})();</script>")


def _sinal_js(sig: str) -> str:
    """A fila se atualiza sozinha: lead novo caindo no rodízio, ou mensagem chegando
    numa conversa da lista, aparecem sem o vendedor recarregar.

    Recarrega em vez de re-renderizar a lista no cliente — é muito menos código, e o
    app inteiro é form + redirect. Mas nunca por cima de trabalho em curso: card
    aberto no deslize, ou campo em foco, adiam pro próximo tique."""
    import json as _json          # local, como no resto do arquivo
    return ("<script>(function(){var sig=" + _json.dumps(sig) + ",ocupado=false;"
            "function tique(){"
            "if(ocupado||document.visibilityState!=='visible')return;"
            "if(document.querySelector('.front.open'))return;"          # deslize aberto
            "var a=document.activeElement;"
            "if(a&&/^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName))return;"
            "ocupado=true;"
            "fetch(window.CKBASE+'/fila/sinal').then(function(r){return r.json();})"
            ".then(function(j){ocupado=false;if(j&&j.ok&&j.sig!==sig)location.reload();})"
            ".catch(function(){ocupado=false;});}"
            "setInterval(tique,8000);"
            "document.addEventListener('visibilitychange',function(){"
            "if(document.visibilityState==='visible')tique();});"
            "})();</script>")


@router.get("/cockpit/agenda", response_class=HTMLResponse)
def cockpit_agenda(request: Request, t: str = ""):
    """A agenda da CONTA, pros três papéis. Era só a do vendedor (as visitas que ELE
    marcou), dono e gestor não tinham aba nenhuma, e a data SEGURADA não aparecia
    pra ninguém no app — a informação que evita prometer a mesma data duas vezes.

    `t` é o seletor Meus × Todos. O padrão diz quem é: o vendedor abre em MEUS —
    a agenda dele é a rota do dia, e abrir com o time inteiro empurraria a próxima
    visita pra baixo da dobra; dono e gestor abrem em TODOS, que é o trabalho deles.
    O dono titular não tem membro_id, então pra ele o seletor nem aparece."""
    sess = _sessao(request)
    g = _gerencia(request)
    if not sess and not g:
        return RedirectResponse("/cockpit/login", status_code=303)
    conta_id, membro_id = sess if sess else g
    gestao = bool(g)
    so_meus = (t == "meus") if t in ("meus", "todos") else (not gestao)
    if not membro_id:
        so_meus = False       # dono titular: "meus" não aponta pra ninguém
    eventos = ck.agenda_da_conta(get_pool(), conta_id, membro_id, so_meus=so_meus)
    hoje = [v for v in eventos if v["hoje"]]

    _TAG = {"visita": ("visita", "var(--azul)", "var(--azul-borda)", "#0d1b23"),
            "segurada": ("segurada", "var(--ambar)", "#5a4520", "#241c0f"),
            "compromisso": ("compromisso", "var(--neon)", "#1e4a3a", "#10241a")}

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
        rot, cor, borda, fundo = _TAG[v["tipo_ev"]]
        tag = (f"<span style='font-size:.62rem;font-weight:700;padding:.08rem .42rem;"
               f"border-radius:10px;border:1px solid {borda};background:{fundo};color:{cor}'>{rot}</span>")
        # quem marcou: 'você' quando é o próprio; vazio quando foi o dono titular
        # (que não tem membro) — aí a linha simplesmente não aparece.
        quem = "você" if v["minha"] else v["autor"]
        prazo = (f" <span style='color:var(--ambar)'>· sinal vence em {esc(v['prazo'])}</span>"
                 if v["prazo"] and v["prazo"] != "vencido"
                 else " <span style='color:var(--coral)'>· prazo vencido</span>" if v["prazo"] else "")
        return (f"<div class='vis{' hoje' if v['hoje'] else ''}'>"
                f"<div class=quando><div class=h>{esc(v['hora'])}</div><div class=d>{esc(v['dia'])}</div></div>"
                f"<div class=mid><b>{esc(v['titulo'])}</b>"
                + (f"<div class=loc>{esc(v['local'])}</div>" if v["local"] else "")
                + f"<div class=loc>{tag}{prazo}" + (f" · {esc(quem)}" if quem else "") + "</div>"
                + (f"<div class=acoes>{''.join(acoes)}</div>" if acoes else "")
                + "</div></div>")

    if eventos:
        miolo = ("<div class=eyebrow>Hoje</div>"
                 + ("".join(bloco(v) for v in hoje) if hoje
                    else "<div class=fonte>Nada marcado pra hoje.</div>"))
        depois = [v for v in eventos if not v["hoje"]]
        if depois:
            miolo += "<div class=eyebrow>Próximos dias</div>" + "".join(bloco(v) for v in depois)
    else:
        miolo = ("<div class=vazio><div class=big>◷</div><b>Nada na agenda</b>"
                 "Marque uma visita pelo lead, ou toque no + pra criar um compromisso.</div>")

    # o seletor só existe pra quem TEM agenda própria (membro_id) — pro dono
    # titular "meus" seria um filtro que devolve sempre vazio.
    seletor = ""
    if membro_id:
        seletor = (
            "<div class=bloco style='margin-top:.9rem'><div class=seg-ag>"
            + (f"<a href='{_BASE}/agenda?t=meus'" + (" class=on" if so_meus else "") + ">Meus</a>")
            + (f"<a href='{_BASE}/agenda?t=todos'" + ("" if so_meus else " class=on") + ">Todos</a>")
            + "</div></div>")

    abas = _abas_dono("agenda") if gestao else _abas_vend("agenda", _pend_vend(conta_id, membro_id))
    corpo = (_hdr("Agenda", f"{len(hoje)} hoje · {len(eventos)} nos próximos 14 dias")
             + _flash(request)
             + f"<div class=scroll>{seletor}{miolo}</div>"
             + f"<a class=fab href='{_BASE}/agenda/novo' aria-label='Novo compromisso'>+</a>"
             + abas)
    return _page("Agenda", corpo)


@router.get("/cockpit/agenda/novo", response_class=HTMLResponse)
def cockpit_agenda_novo_tela(request: Request):
    """Compromisso avulso direto do celular. Antes o app só criava evento por DENTRO
    de um lead (a visita) — reunião, entrega ou qualquer coisa fora de lead não tinha
    onde entrar sem abrir o desktop."""
    if not (_sessao(request) or _gerencia(request)):
        return RedirectResponse("/cockpit/login", status_code=303)
    corpo = (_hdr("Novo compromisso", "entra na agenda de todos", voltar=f"{_BASE}/agenda")
             + _flash(request)
             + f"<form class=telaform method=post action='{_BASE}/agenda/novo'>"
             + "<div class=scroll><div class=secao><div class='fic'>"
             + "<label class=fic-c><span>O que é</span>"
               "<input name=titulo required autocomplete=off autofocus"
               " placeholder='Ex: Reunião — Salão Prime'></label>"
             + "<label class='fic-c meia'><span>Data</span>"
               "<input name=data type=date required></label>"
             + "<label class='fic-c meia'><span>Hora</span>"
               "<input name=hora type=time required></label>"
             + "<label class=fic-c><span>Local (opcional)</span>"
               "<input name=local autocomplete=off placeholder='Endereço ou link'></label>"
             + "</div><div class=fonte>Aparece pra equipe inteira, com o seu nome. Visita "
               "de lead continua sendo marcada pelo próprio lead — ali ela já sai ligada "
               "na ficha.</div></div></div>"
             + "<div class=rodape-b><button class=btn type=submit>Marcar</button></div>"
             + "</form>")
    return _page("Novo compromisso", corpo)


@router.post("/cockpit/agenda/novo")
def cockpit_agenda_novo(request: Request, titulo: str = Form(""), data: str = Form(""),
                        hora: str = Form(""), local: str = Form("")):
    sess = _sessao(request)
    g = _gerencia(request)
    if not sess and not g:
        return RedirectResponse("/cockpit/login", status_code=303)
    conta_id, membro_id = sess if sess else g
    from finance import agenda as ag
    inicio = ag.parse_datahora(f"{(data or '').strip()} {(hora or '').strip()}".strip())
    if not (titulo or "").strip() or not inicio:
        request.session["ck_err"] = "Preencha o que é, a data e a hora."
        return RedirectResponse(f"{_BASE}/agenda/novo", status_code=303)
    ag.criar_evento(get_pool(), conta_id, (titulo or "").strip()[:200], inicio,
                    membro_id=membro_id, local=(local or "").strip() or None)
    request.session["ck_ok"] = "Compromisso marcado ✓"
    return RedirectResponse(f"{_BASE}/agenda", status_code=303)


@router.get("/cockpit/resultado", response_class=HTMLResponse)
def cockpit_resultado(request: Request):
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

    # A comissão é sobre o que o cliente PAGOU — mesma conta do relatório do dono
    # (finance/comissao.py). O funil fica separado, embaixo, como previsão.
    if r["comissao_centavos"] is not None:
        pct = f"{r['comissao_pct']:g}".replace(".", ",")
        comissao = (f"<div class='kpi hero'><div class=v>{esc(_brl(r['comissao_centavos'], centavos_visiveis=True))}</div>"
                    f"<div class=l>Sua comissão</div><div class=d>{pct}% de "
                    f"{esc(_brl(r['recebido_centavos']))} que entraram</div></div>")
    else:
        comissao = ("<div class=bloco><div class=card style='font-size:.82rem;color:var(--text-dim)'>"
                    "Sua <b>% de comissão</b> ainda não foi configurada. Quem define é o dono, "
                    "em Equipe → comissão.</div></div>")

    pos = (f"<div class=kpi><div class=v>{r['posicao']}º</div><div class=l>No placar</div>"
           f"<div class=d>entre {r['total_equipe']} da equipe</div></div>") if r["posicao"] else ""

    corpo = (
               _hdr("Meu resultado", "o que entrou e o que está por vir")
             + f"<div class=scroll><div class=seg>{seg('hoje','Hoje')}{seg('semana','Semana')}{seg('mes','Mês')}</div>"
             + comissao
             + "<div class=kpis>"
             + f"<div class=kpi><div class=v>{esc(_brl(r['recebido_centavos']))}</div>"
               f"<div class=l>Recebido</div><div class=d>{r['n_vendas']} pagamento(s) no período</div></div>"
             + f"<div class=kpi><div class=v>{esc(_brl(r['fechado_centavos']))}</div>"
               f"<div class=l>No funil</div><div class=d>{r['ganhos']} ganho(s) · previsão</div></div>"
             + "</div>"
             + "<div class=fonte><b>Comissão sai do recebido</b> — quando o cliente paga, seja "
               "no caixa ou na baixa do título. É a mesma conta do relatório do dono, então os "
               "dois números batem.<br>O <b>funil</b> é o valor que você estimou nos leads que "
               "marcou como ganho: serve pra você acompanhar o que vem, e não entra na comissão "
               "enquanto o contrato não for fechado e pago.</div>"
             + "<div class=eyebrow>Seu ritmo</div>"
             + "<div class=kpis>"
             + f"<div class=kpi><div class=v>{esc(r['conversao'])}</div><div class=l>Conversão</div>"
               "<div class=d>ganhos vs. perdidos</div></div>"
             + f"<div class=kpi><div class=v>{r['fila']}</div><div class=l>Na fila</div>"
               "<div class=d>leads abertos com você</div></div>"
             + f"<div class=kpi><div class=v>{esc(r['resp'])}</div><div class=l>Resposta</div>"
               "<div class=d>média de 30 dias</div></div>"
             + pos + "</div>"
             + "</div>" + _abas_vend("resultado", _pend_vend(conta_id, membro_id)))
    return _page("Meu resultado", corpo)


# ------------------------------------------------------------------ montar orçamento
_ORC_JS = r"""
<script>
(function(){
  var O=window.ORC||{cat:[],leadId:0,base:"",desc:false};
  // sel[i] = {q, desc_tipo, desc_val} — quantidade E desconto no MESMO lugar. Mapa
  // paralelo casado por índice seria a armadilha que a 162 tirou dos títulos.
  // Os NOMES são os mesmos que o avulso usa e os mesmos que vão no payload: um
  // campo com dois nomes pelo caminho é onde o desconto se perde na tradução.
  var sel={}, avulsos=[], dFim={t:"pct",v:0};
  function $(id){return document.getElementById(id);}
  function brl(v){return "R$ "+Number(v||0).toLocaleString("pt-BR");}
  function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(m){
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[m];});}
  function toast(m){var t=$("toast");if(!t)return;t.textContent=m;t.classList.add("show");
    clearTimeout(t._t);t._t=setTimeout(function(){t.classList.remove("show");},2200);}
  function preco(s){var a=[];if(s.setup)a.push(brl(s.setup));if(s.mensal)a.push(brl(s.mensal)+"/mês");
    return a.join(" + ")||"grátis";}

  // ---- a conta, espelhando finance/desconto.py LINHA A LINHA ----
  // Em CENTAVOS, como o Python — arredondar em reais e em centavos dá números
  // diferentes, e a divergência é invisível na tela. O servidor refaz tudo e é
  // ele quem vale; isto aqui é pra o vendedor ver o mesmo número que vai gravar.
  var MESES=12;
  // Python arredonda metade pro PAR (round-half-even) e o JS arredonda pra cima.
  // Sem isto, um desconto que caia exatamente no meio separa as duas contas.
  function arr(x){
    var f=Math.floor(x), d=x-f;
    if(d>0.5)return f+1;
    if(d<0.5)return f;
    return (f%2===0)?f:f+1;
  }
  function contribuicao(it){return (it.setup||0)*100+(it.mensal||0)*100*MESES;}
  function pctLinha(it){
    var base=contribuicao(it);
    if(base<=0)return 0;
    var v=Math.max(0,it.desc_val||0);
    if(v<=0)return 0;
    if((it.desc_tipo||"pct")==="pct")return Math.min(100,v);
    return Math.min(100,100*(v*100)/base);        // reais viram o % da linha
  }
  function quantoDesconta(base,tipo,pct,valor){
    base=Math.max(0,base);
    if(base<=0)return 0;
    var d=(tipo==="valor")?Math.max(0,valor):arr(base*Math.min(100,Math.max(0,pct))/100);
    return Math.max(0,Math.min(base,d));
  }
  function conta(){
    var its=itens(), bs=0,bm=0,ls=0,lm=0;
    its.forEach(function(it){
      var s=Math.max(0,it.setup||0)*100, m=Math.max(0,it.mensal||0)*100, p=pctLinha(it);
      bs+=s; bm+=m;
      ls+=arr(s*(100-p)/100); lm+=arr(m*(100-p)/100);
    });
    var subtotal=ls+lm*MESES;
    var bruto=bs+bm*MESES;
    var df=O.desc?quantoDesconta(subtotal,dFim.t,dFim.v,dFim.v*100):0;
    return {n:its.length, bruto:bruto, subtotal:subtotal,
            descItens:bruto-subtotal, descFim:df, total:subtotal-df,
            setup:ls, mensal:lm};
  }

  function ctrl(k,it){
    if(!O.desc)return "";
    var p=(it.desc_tipo||"pct")==="pct";
    var tag="";
    var pc=pctLinha(it);
    if(pc>0){var d=arr(contribuicao(it)*pc/100);tag='<span class=tag>− '+brl(Math.round(d/100))+'</span>';}
    return '<div class=dsc data-k="'+k+'"><span class=pil>'
      +'<button type=button data-dt="pct" class="'+(p?"on":"")+'">%</button>'
      +'<button type=button data-dt="valor" class="'+(p?"":"on")+'">R$</button></span>'
      +'<input class=cmp data-di="'+k+'" inputmode=numeric autocomplete=off'
      +' aria-label="Desconto do item" value="'+(it.desc_val||"")+'"></div>';
  }
  function precoCel(it){
    var p=pctLinha(it);
    if(p<=0)return preco(it);
    var liq={setup:Math.round(arr((it.setup||0)*100*(100-p)/100)/100),
             mensal:Math.round(arr((it.mensal||0)*100*(100-p)/100)/100)};
    return preco(liq)+'<s>'+preco(it)+'</s>';
  }

  function pintaCatalogo(){
    var box=$("cat");if(!box)return;box.innerHTML="";
    if(!O.cat.length){box.innerHTML='<div class=vazio-cat>Nenhum serviço no catálogo ainda. '
      +'Dá pra montar com itens avulsos aqui embaixo, ou pedir pro gestor cadastrar o catálogo no painel.</div>';return;}
    O.cat.forEach(function(s,i){
      var e=sel[i], on=e!==undefined;
      var q=on?'<div class=qtd><button data-q="-" data-i="'+i+'" aria-label="menos">−</button>'
        +'<span>'+e.q+'</span><button data-q="+" data-i="'+i+'" aria-label="mais">+</button></div>':'';
      var it=on?linhaDe(i):s;
      var d=document.createElement("div");d.className="srv"+(on?" on":"");d.setAttribute("data-i",i);
      d.innerHTML='<div class=ck>'+(on?'✓':'')+'</div><div class=m><b>'+esc(s.nome)+'</b>'
        +(s.desc?'<small>'+esc(s.desc)+'</small>':'')+q+(on?ctrl("c"+i,it):"")
        +'</div><div class=pr>'+(on?precoCel(it):preco(s))+'</div>';
      box.appendChild(d);
    });
  }
  // "c3" = 3ª linha do catálogo, "a1" = 2º avulso. Uma porta só: o controle é o
  // mesmo nos dois, e dois caminhos separados divergiriam no primeiro ajuste.
  function alvo(k){return k.charAt(0)==="c"?sel[k.slice(1)]:avulsos[k.slice(1)];}
  function itemDe(k){return k.charAt(0)==="c"?linhaDe(k.slice(1)):avulsos[k.slice(1)];}
  function linhaDe(i){
    var s=O.cat[i], e=sel[i];
    return {nome:s.nome+(e.q>1?" (× "+e.q+")":""), setup:(s.setup||0)*e.q,
            mensal:(s.mensal||0)*e.q, desc_tipo:e.desc_tipo, desc_val:e.desc_val};
  }
  function itens(){
    var out=[];
    Object.keys(sel).forEach(function(i){out.push(linhaDe(i));});
    avulsos.forEach(function(c){out.push(c);});
    return out;
  }
  function soma(){
    var t=conta();
    $("gerar").disabled=t.n===0;
    if(!t.n){$("total").innerHTML="—";
      ["dfim","lsub","ldesc"].forEach(function(x){$(x).style.display="none";});return;}
    var temD=(t.descItens+t.descFim)>0;
    $("dfim").style.display=O.desc?"flex":"none";
    $("lsub").style.display=temD?"flex":"none";
    $("ldesc").style.display=t.descFim>0?"flex":"none";
    $("sub").textContent=brl(Math.round(t.subtotal/100));
    $("ldescr").textContent="Desconto no total"+(dFim.t==="pct"&&dFim.v>0?" ("+dFim.v+"%)":"");
    $("descv").textContent="− "+brl(Math.round(t.descFim/100));
    $("total").innerHTML=brl(Math.round(t.total/100))
      +(temD?'<s>'+brl(Math.round(t.bruto/100))+'</s>':"");
  }

  document.addEventListener("click",function(e){
    // a pílula %/R$ e o campo vivem DENTRO da linha: sem parar o clique aqui,
    // mexer no desconto desmarcaria o serviço.
    var dt=e.target.closest("[data-dt]");
    if(dt){e.stopPropagation();
      var k=dt.closest(".dsc").getAttribute("data-k");
      if(k){alvo(k).desc_tipo=dt.getAttribute("data-dt");
        pintaCatalogo();pintaAvulsos();soma();}
      return;}
    if(e.target.closest(".cmp")){e.stopPropagation();return;}
    var qb=e.target.closest("[data-q]");
    if(qb){e.stopPropagation();var i=qb.getAttribute("data-i");
      sel[i].q=Math.max(1,sel[i].q+(qb.getAttribute("data-q")==="+"?1:-1));pintaCatalogo();soma();return;}
    var row=e.target.closest(".srv");
    if(row){var j=row.getAttribute("data-i");
      if(sel[j]!==undefined)delete sel[j];else sel[j]={q:1,desc_tipo:"pct",desc_val:0};
      pintaCatalogo();soma();}
  });
  // o rodapé é só o desconto do total — pílula própria, mesmo desenho
  document.addEventListener("click",function(e){
    var b=e.target.closest("[data-dfim]");if(!b)return;
    dFim.t=b.getAttribute("data-dfim");
    Array.prototype.forEach.call(b.parentNode.children,function(x){
      x.classList.toggle("on",x===b);});
    soma();
  });
  // NÃO repinta o catálogo no input: repintar tira o foco do campo a cada tecla.
  // Só o preço da linha e o rodapé mudam.
  document.addEventListener("input",function(e){
    var f=e.target.closest("[data-di]");
    if(f){
      var k=f.getAttribute("data-di"), v=parseInt((f.value||"").replace(/\D/g,''),10);
      alvo(k).desc_val=Math.max(0,isNaN(v)?0:v);
      var it=itemDe(k), i=k.slice(1);
      var row=(k.charAt(0)==="c")
        ? $("cat").querySelector('.srv[data-i="'+i+'"]')
        : $("avulsos").querySelector('.avl[data-a="'+i+'"]');
      if(row)row.querySelector(k.charAt(0)==="c"?".pr":".vl").innerHTML=precoCel(it);
      var tg=f.closest(".dsc").querySelector(".tag");
      var pc=pctLinha(it);
      var txt=pc>0?("− "+brl(Math.round(arr(contribuicao(it)*pc/100)/100))):"";
      if(!tg&&txt){tg=document.createElement("span");tg.className="tag";
        f.closest(".dsc").appendChild(tg);}
      if(tg)tg.textContent=txt;
      soma();return;
    }
    if(e.target.id==="dfimv"){
      var w=parseInt((e.target.value||"").replace(/\D/g,''),10);
      dFim.v=Math.max(0,isNaN(w)?0:w);soma();
    }
  });

  // O item avulso precisa APARECER. No app anterior ele entrava só na conta do
  // total: quem digitava errado não via o erro e não tinha como desfazer.
  function pintaAvulsos(){
    var box=$("avulsos");if(!box)return;
    box.innerHTML=avulsos.map(function(a,i){
      return '<div class=avl data-a="'+i+'"><b>'+esc(a.nome)+'</b>'
        +'<span class=vl>'+precoCel(a)+'</span>'
        +'<button type=button data-x="'+i+'" aria-label="Remover '+esc(a.nome)+'">×</button>'
        +ctrl("a"+i,a)+'</div>';
    }).join("");
  }
  document.addEventListener("click",function(e){
    var x=e.target.closest("[data-x]");if(!x)return;
    avulsos.splice(parseInt(x.getAttribute("data-x"),10),1);
    pintaAvulsos();soma();
  });
  var add=$("addbtn");
  if(add)add.onclick=function(){
    var n=$("addnome").value.trim();
    var v=parseInt(($("addval").value||"").replace(/\D/g,''),10);
    if(!n||!v){toast("Preencha o nome e o valor");return;}
    // o avulso leva desconto igual ao item de catálogo: o vendedor põe o preço
    // cheio ("Visita técnica R$ 400") e negocia por cima, e aí o cliente vê o
    // riscado na folha em vez de um valor que apareceu já abatido do nada.
    avulsos.push({nome:n,setup:v,mensal:0,desc_tipo:"pct",desc_val:0});
    $("addnome").value="";$("addval").value="";pintaAvulsos();soma();toast("Item adicionado");
  };
  var g=$("gerar");
  if(g)g.onclick=function(){
    g.disabled=true;g.textContent="Gerando…";
    // manda o BRUTO e o que foi digitado: o servidor refaz a conta do desconto.
    // Mandar o já descontado faria ele descontar de novo.
    fetch(O.base+"/lead/"+O.leadId+"/orcamento",{method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({itens:itens(),desconto:{tipo:dFim.t,pct:dFim.v,valor:dFim.v}})})
      .then(function(r){return r.json();}).then(function(j){
        if(!j||!j.ok){toast((j&&j.erro)||"Não deu certo");g.disabled=false;
          g.textContent="Gerar proposta e link";return;}
        pronto(j);
      }).catch(function(){toast("Falha de conexão");g.disabled=false;
        g.textContent="Gerar proposta e link";});
  };
  function pronto(j){
    $("build").style.display="none";$("rodape").style.display="none";
    // sem o atalho pro WhatsApp (conta que entrega tudo pelo Zaq) o "Enviar na
    // conversa" vira a ação principal — botão fantasma sozinho parece opcional
    var wa=j.zap?'<a class=btn href="'+esc(j.zap)+'" target=_blank rel=noopener>Mandar no WhatsApp</a>':'';
    var classeEnviar=j.zap?'btn ghost':'btn';
    $("pronto").innerHTML='<div class=pronto><div class=big>✓</div><h3>Proposta pronta</h3>'
      +'<p>Mande o link pro cliente — ele abre, vê com a marca da empresa e aprova online.</p>'
      +wa
      +'<button class="'+classeEnviar+'" style="margin-top:.5rem" id=naconversa>Enviar na conversa do lead</button>'
      +'<div class=copiar><input value="'+esc(j.link)+'" readonly onclick="this.select()">'
      +'<button type=button id=copiar>Copiar</button></div>'
      +'<a class="btn ghost" style="margin-top:.9rem" href="'+O.base+'/lead/'+O.leadId+'">Voltar pro lead</a></div>';
    $("pronto").style.display="block";
    $("copiar").onclick=function(){navigator.clipboard.writeText(j.link).then(
      function(){toast("Link copiado");},function(){toast("Selecione e copie");});};
    $("naconversa").onclick=function(){
      var b=this;b.disabled=true;b.textContent="Enviando…";
      fetch(O.base+"/lead/"+O.leadId+"/orcamento/enviar",{method:"POST",
        headers:{"Content-Type":"application/json"},body:JSON.stringify({link:j.link})})
        .then(function(r){return r.json();}).then(function(x){
          toast(x&&x.ok?"Enviado na conversa":(x&&x.erro)||"Não consegui enviar agora");
          b.disabled=false;b.textContent="Enviar na conversa do lead";})
        .catch(function(){toast("Falha de conexão");b.disabled=false;
          b.textContent="Enviar na conversa do lead";});};
  }
  // exposto pro teste de paridade rodar a MESMA conta que a tela roda
  window.__orc={conta:conta,itens:itens,sel:sel,avulsos:avulsos,dFim:dFim,
                set:function(s,a,d){sel=s;avulsos=a;dFim=d;}};
  pintaCatalogo();soma();
})();
</script>"""


@router.get("/cockpit/lead/{lead_id}/orcamento", response_class=HTMLResponse)
def cockpit_orcamento_montar(request: Request, lead_id: int):
    """Montador da proposta. O motor é o mesmo de sempre (`cockpit.criar_orcamento`);
    o que mudou foi a casca. Ela precisava vir junto: montar o orçamento é o meio do
    caminho de fechar venda, e deixar essa tela pra trás faria o vendedor trocar de
    visual bem na hora que mais importa."""
    sess = _sessao(request)
    if not sess:
        return RedirectResponse("/cockpit/login", status_code=303)
    conta_id, membro_id = sess
    pool = get_pool()
    d = ck.lead_do_vendedor(pool, conta_id, membro_id, lead_id)
    if not d:
        return RedirectResponse(_BASE, status_code=303)
    import json as _json
    cat = ck.catalogo_servicos(pool, conta_id)
    # O DESCONTO É DO NICHO DE SERVIÇOS. Esconder da tela é metade do portão — a
    # outra metade está no `criar_orcamento`, que descarta os campos de quem não
    # vende serviço. O form vem do navegador, e navegador não é fonte confiável.
    # `</` escapado: o catálogo é texto do usuário e fecharia o <script> antes da hora
    dados = _json.dumps({"cat": cat, "leadId": lead_id, "base": _BASE,
                         "desc": ck.vende_servico(pool, conta_id)},
                        ensure_ascii=False).replace("</", "<\\/")
    corpo = (_hdr("Orçamento", d["empresa"], voltar=f"{_BASE}/lead/{lead_id}")
             + "<div class=toast id=toast></div>"
             + "<div class=scroll id=build>"
             + "<div class=secao><div class=rot>Serviços do catálogo</div></div><div id=cat></div>"
             + "<div class=secao><div class=rot>Adicionar item avulso</div></div>"
             + "<div class=avulso><input id=addnome placeholder='Ex.: Visita técnica' autocomplete=off>"
               "<input class=v id=addval inputmode=numeric placeholder='R$' autocomplete=off>"
               "<button type=button id=addbtn aria-label='Adicionar item'>+</button></div>"
             + "<div id=avulsos></div>"
             + "</div>"
             + "<div class=rodape-b id=rodape>"
               "<div class=dlin id=dfim style='display:none'><span>Desconto no total</span>"
               "<span class=dsc><span class=pil>"
               "<button type=button data-dfim=pct class=on>%</button>"
               "<button type=button data-dfim=valor>R$</button></span>"
               "<input class=cmp id=dfimv inputmode=numeric autocomplete=off aria-label='Desconto no total'>"
               "</span></div>"
               "<div class=dlin id=lsub style='display:none'><span>Itens</span><b id=sub></b></div>"
               "<div class='dlin desc' id=ldesc style='display:none'><span id=ldescr>Desconto</span>"
               "<b id=descv></b></div>"
               "<div class=tot><span>Total</span><b id=total>—</b></div>"
               "<button class=btn id=gerar type=button disabled>Gerar proposta e link</button></div>"
             + "<div id=pronto style='display:none'></div>"
             + f"<script>window.ORC={dados};</script>" + _ORC_JS)
    return _page(f"Orçamento — {d['empresa']}", corpo)


@router.post("/cockpit/lead/{lead_id}/orcamento")
async def cockpit_orcamento_criar(request: Request, lead_id: int, payload: dict = Body(...)):
    sess = _sessao(request)
    if not sess:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    p = payload or {}
    r = ck.criar_orcamento(get_pool(), sess[0], sess[1], lead_id,
                           p.get("itens"), p.get("desconto"))
    return JSONResponse(r)


@router.post("/cockpit/lead/{lead_id}/orcamento/enviar")
async def cockpit_orcamento_enviar_conversa(request: Request, lead_id: int, payload: dict = Body(...)):
    sess = _sessao(request)
    if not sess:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    link = (payload or {}).get("link", "")
    if not link:
        return JSONResponse({"ok": False, "erro": "sem link"})
    return JSONResponse(ck.enviar_proposta_conversa(get_pool(), sess[0], sess[1], lead_id, link))


# ------------------------------------------------------------------ agendar visita
_DIA_SEM = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]
_HORAS = ["08:00", "09:00", "10:00", "11:00", "13:00", "14:00",
          "15:00", "16:00", "17:00", "18:00", "19:00"]

_VISITA_JS = r"""
<script>
(function(){
  var V=window.VISITA||{leadId:0,dias:[],horas:[],base:""};
  var st={dia:(V.dias[0]||{}).iso,hora:"10:00",dur:60,lembr:60};
  var DURS=[["30 min",30],["1 h",60],["1h30",90],["2 h",120]];
  var LEMBR=[["Sem lembrete",0],["1h antes",60],["1 dia antes",1440]];
  function $(id){return document.getElementById(id);}
  function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(m){
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[m];});}
  function toast(m){var t=$("toast");if(!t)return;t.textContent=m;t.classList.add("show");
    clearTimeout(t._t);t._t=setTimeout(function(){t.classList.remove("show");},2200);}

  function pinta(){
    $("dias").innerHTML=V.dias.map(function(d){
      return '<div class="esc'+(d.iso===st.dia?' on':'')+'" data-d="'+d.iso+'">'+d.lab+'</div>';}).join("");
    $("horas").innerHTML=V.horas.map(function(h){
      return '<div class="esc'+(h===st.hora?' on':'')+'" data-h="'+h+'">'+h+'</div>';}).join("");
    $("durs").innerHTML=DURS.map(function(o){
      return '<div class="esc'+(o[1]===st.dur?' on':'')+'" data-dur="'+o[1]+'">'+o[0]+'</div>';}).join("");
    $("lembr").innerHTML=LEMBR.map(function(o){
      return '<div class="esc'+(o[1]===st.lembr?' on':'')+'" data-lb="'+o[1]+'">'+o[0]+'</div>';}).join("");
    resumo();
  }
  function diaLab(){
    var d=V.dias.filter(function(x){return x.iso===st.dia;})[0];
    return d?d.lab.toLowerCase():st.dia;
  }
  function resumo(){
    var on=!$("avisar").classList.contains("off");
    $("previa").style.display=on?"block":"none";
    $("previa").textContent="Olá! 👋 Sua visita ao "+V.nome+" está marcada:\n📅 "+diaLab()
      +" às "+st.hora+"\n📍 "+$("endereco").value+"\nAté lá!";
    // esc() em V.quem e V.nome: os dois são texto do cliente (contato do lead e
    // nome fantasia da conta) e aqui entram por innerHTML.
    $("resumo").innerHTML='<b>'+diaLab()+' às '+st.hora+'</b> · '+esc(V.quem)+' vem ao '+esc(V.nome);
  }
  document.addEventListener("click",function(e){
    var t=e.target.closest(".esc");if(!t)return;
    if(t.dataset.d!=null)st.dia=t.dataset.d;
    else if(t.dataset.h!=null)st.hora=t.dataset.h;
    else if(t.dataset.dur!=null)st.dur=parseInt(t.dataset.dur,10);
    else if(t.dataset.lb!=null)st.lembr=parseInt(t.dataset.lb,10);
    else return;
    pinta();
  });
  $("avisar").onclick=function(){
    var off=this.classList.toggle("off");this.textContent=off?"Desligado":"Ligado";resumo();};
  $("endereco").oninput=resumo;
  $("agendar").onclick=function(){
    var b=this;b.disabled=true;b.textContent="Agendando…";
    fetch(V.base+"/lead/"+V.leadId+"/visita",{method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({data:st.dia,hora:st.hora,dur:st.dur,lembrete:st.lembr,
        local:$("endereco").value,avisar:!$("avisar").classList.contains("off")})})
      .then(function(r){return r.json();}).then(function(j){
        if(!j||!j.ok){toast((j&&j.erro)||"Não deu certo");b.disabled=false;
          b.textContent="Agendar visita";return;}
        pronto(j);
      }).catch(function(){toast("Falha de conexão");b.disabled=false;b.textContent="Agendar visita";});
  };
  function pronto(j){
    $("build").style.display="none";$("rodape").style.display="none";
    var avisado=j.avisado?'<div class=ok>Confirmação e convite enviados pro cliente</div>':'';
    var wa=j.zap?'<a class="btn ghost" style="margin-top:.5rem" href="'+j.zap
      +'" target=_blank rel=noopener>Reenviar pro cliente no WhatsApp</a>':'';
    $("pronto").innerHTML='<div class=pronto><div class=big>✓</div><h3>Visita agendada</h3>'
      +'<div class=evcard><div class=q>'+esc(j.quando)+'</div>'
      +'<div class=l>'+esc(j.empresa)+'</div><div class=l>'+esc(j.local)+'</div>'
      +'<div class=o>Visitante: '+esc(V.quem)+' · o lead foi pra Qualificado</div></div>'
      +'<div class=ok>Entrou na sua agenda</div>'+avisado
      +'<a class="btn ghost" href="'+esc(j.ics_url)+'" target=_blank rel=noopener>Baixar o convite (.ics)</a>'
      +wa
      +'<a class="btn ghost" style="margin-top:.9rem" href="'+V.base+'/lead/'+V.leadId+'">Voltar pro lead</a></div>';
    $("pronto").style.display="block";
  }
  pinta();
})();
</script>"""


@router.get("/cockpit/lead/{lead_id}/visita", response_class=HTMLResponse)
def cockpit_visita_marcar(request: Request, lead_id: int):
    """Agendar a visita do cliente ao espaço da empresa. Motor: `cockpit.agendar_visita`
    (cria o evento, liga no lead, move pra Qualificado e manda o .ics pro cliente)."""
    sess = _sessao(request)
    if not sess:
        return RedirectResponse("/cockpit/login", status_code=303)
    conta_id, membro_id = sess
    pool = get_pool()
    d = ck.lead_do_vendedor(pool, conta_id, membro_id, lead_id)
    if not d:
        return RedirectResponse(_BASE, status_code=303)
    from datetime import datetime, timedelta
    from finance import agenda as ag
    import json as _json
    esp = ck.endereco_empresa(pool, conta_id)
    hoje = datetime.now(ag.BRT).date()
    dias = []
    for i in range(14):
        dt = hoje + timedelta(days=i)
        lab = "Hoje" if i == 0 else "Amanhã" if i == 1 else f"{_DIA_SEM[dt.weekday()]} {dt.day}"
        dias.append({"iso": dt.isoformat(), "lab": lab})
    quem = d.get("contato") or d.get("empresa") or "o cliente"
    dados = _json.dumps({"leadId": lead_id, "dias": dias, "horas": _HORAS,
                         "nome": esp["nome"], "quem": quem, "base": _BASE},
                        ensure_ascii=False).replace("</", "<\\/")
    endereco = esp["endereco"] or esp["nome"]
    mapa = (f"<a href='{esc(esp['maps'])}' target=_blank rel=noopener>Abrir no Google Maps</a>"
            if esp["maps"] else "")
    corpo = (_hdr("Agendar visita", d["empresa"], voltar=f"{_BASE}/lead/{lead_id}")
             + "<div class=toast id=toast></div>"
             + "<div class=scroll id=build>"
             + f"<div class=secao><div class=rot>Quem vem visitar</div>"
               f"<div class=local><div class=nome>{esc(quem)}</div></div></div>"
             + "<div class=secao><div class=rot>Dia</div><div class=escolhas id=dias></div></div>"
             + "<div class=secao><div class=rot>Horário</div>"
               "<div class='escolhas horas' id=horas></div></div>"
             + "<div class=secao><div class=rot>Duração</div><div class=escolhas id=durs></div></div>"
             + "<div class=secao><div class=rot>Onde — o seu espaço</div><div class=local>"
               f"<div class=nome>{esc(esp['nome'])}</div>"
               f"<div class=end>{esc(esp['endereco'] or 'sem endereço no cadastro da empresa')}</div>{mapa}"
               f"<input id=endereco value='{esc(endereco)}' aria-label='Endereço da visita'></div>"
               "<div class=fonte>Vem do cadastro da empresa — edite se a visita for em outra unidade.</div></div>"
             + "<div class=secao><div class=rot>Lembrete pra você</div>"
               "<div class=escolhas id=lembr></div></div>"
             + "<div class=secao><div class=linha-tgl><div class=t>Avisar o cliente no WhatsApp"
               "<small>confirmação e convite pro calendário dele</small></div>"
               "<div class=tgl id=avisar>Ligado</div></div>"
               "<div class=msg-previa id=previa></div></div>"
             + "</div>"
             + "<div class=rodape-b id=rodape><div class=tot><span id=resumo>—</span></div>"
               "<button class=btn id=agendar type=button>Agendar visita</button></div>"
             + "<div id=pronto style='display:none'></div>"
             + f"<script>window.VISITA={dados};</script>" + _VISITA_JS)
    return _page(f"Visita — {d['empresa']}", corpo)


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


def _iso(d) -> str:
    """date -> 'AAAA-MM-DD' pro <input type=date>, que só aceita esse formato."""
    return d.isoformat() if hasattr(d, "isoformat") else (d or "")


# Autopreenchimento pelo CEP. A rota /api/cep/{cep} JÁ EXISTE (web/portal.py) e usa a
# BrasilAPI por dentro (finance/cep.py) — não há rota nova aqui. Ela passa livre pelo
# gate de web/app.py, que só guarda /painel* e /membros*.
#
# Duas decisões copiadas do admCep (web/admin.py), porque o erro oposto é caro:
#   * só preenche campo VAZIO — quem já digitou o endereço não pode vê-lo sumir
#     porque o CEP amplo do bairro devolveu outra rua;
#   * .catch silencioso — a BrasilAPI fora do ar não pode travar o preenchimento da
#     ficha. O consultar() já devolve None em qualquer falha e a rota responde
#     {"ok": false}; aqui o que resta é não estourar no console e seguir na mão.
_CEP_JS = """<script>(function(){
  var cep=document.getElementById('fic-cep'); if(!cep) return;
  function po(id,v){var e=document.getElementById(id); if(e&&!e.value.trim()&&v) e.value=v;}
  cep.addEventListener('input',function(){
    var d=(cep.value||'').replace(/[^0-9]/g,'');
    if(d.length!==8) return;
    fetch('/api/cep/'+d).then(function(r){return r.json();}).then(function(j){
      if(!j||!j.ok) return;
      po('fic-endereco',j.rua); po('fic-bairro',j.bairro);
      po('fic-cidade',j.cidade); po('fic-uf',j.uf);
      var n=document.getElementById('fic-numero'); if(n&&!n.value.trim()) n.focus();
    }).catch(function(){});
  });
})();</script>"""


# ------------------------------------------------------------------ ficha do cliente
@router.get("/cockpit/lead/{lead_id}/ficha", response_class=HTMLResponse)
def cockpit_ficha_tela(request: Request, lead_id: int):
    """Os dados do cliente, preenchidos por quem está conversando com ele. O lead entra
    pelo WhatsApp com um número e mais nada; nome, CPF e e-mail aparecem no meio da
    conversa, e quem descobre é o vendedor — não o gestor no painel de desktop.

    Tela inteira, no mesmo molde de orçamento e visita: formulário não cabe na folha
    de ações (ver o comentário em `_lead_vendedor`)."""
    sess = _sessao(request)
    if not sess:
        return RedirectResponse("/cockpit/login", status_code=303)
    conta_id, membro_id = sess
    d = ck.lead_do_vendedor(get_pool(), conta_id, membro_id, lead_id)
    if not d:
        return RedirectResponse(_BASE, status_code=303)

    def campo(nome, rot, valor, *, tipo="text", modo="", meia=False):
        # id=fic-<nome> porque o autopreenchimento do CEP precisa achar rua, bairro,
        # cidade e UF pra completar; sem id o JS teria que caçar por name= no form.
        extra = f" inputmode={modo}" if modo else ""
        return (f"<label class='fic-c{' meia' if meia else ''}'><span>{esc(rot)}</span>"
                f"<input id=fic-{nome} name={nome} type={tipo}{extra} value='{esc(valor or '')}'"
                f" autocomplete=off></label>")

    # Rótulo único: quem digita não escolhe a coluna — o tamanho do documento decide
    # (11 = CPF, 14 = CNPJ) lá no _doc_lead. Pedir "CNPJ" pra uma cliente pessoa
    # física seria empurrar o vendedor pro campo errado.
    campos = (
        campo("empresa", "Nome / empresa", d.get("empresa"))
        + campo("contato", "Quem fala com você", d.get("contato"))
        + campo("cargo", "Cargo", d.get("cargo"), meia=True)
        + campo("segmento", "Segmento", d.get("segmento"), meia=True)
        # WhatsApp e Telefone são COLUNAS DIFERENTES do lead, e a ficha mostrava só a
        # segunda. Quem entra por `whatsapp_inbound` nasce com o número em `whatsapp` e
        # `telefone` NULL (painel_prospeccao, insert do lead novo) — ou seja, justamente
        # no lead que veio conversando o campo aparecia EM BRANCO, com o número logo ali.
        # Os dois juntos porque são coisas diferentes na prática: o zap é por onde a
        # conversa corre, o fixo/comercial é o que o vendedor descobre depois.
        + campo("whatsapp", "WhatsApp", d.get("whatsapp"), tipo="tel", modo="tel", meia=True)
        + campo("telefone", "Telefone", d.get("telefone"), tipo="tel", modo="tel", meia=True)
        + campo("documento", "CPF ou CNPJ", d.get("doc_fmt"), modo="numeric", meia=True)
        + campo("email", "E-mail", d.get("email"), tipo="email", modo="email")
        # O CEP vem ANTES do endereço de propósito: digitou os 8 dígitos, rua/bairro/
        # cidade/UF se preenchem sozinhos (ver _CEP_JS) e sobra o número pra digitar.
        + campo("cep", "CEP", d.get("cep"), modo="numeric", meia=True)
        + campo("numero", "Número", d.get("numero"), meia=True)
        + campo("endereco", "Endereço", d.get("endereco"))
        + campo("bairro", "Bairro", d.get("bairro"), meia=True)
        # <input type=date> abre o seletor nativo do celular — datilografar
        # dd/mm/aaaa numa mão, na rua, ninguém faz.
        + campo("nascimento", "Aniversário", _iso(d.get("nascimento")), tipo="date", meia=True)
        + campo("cidade", "Cidade", d.get("cidade"), meia=True)
        + campo("uf", "UF", d.get("uf"), meia=True)
        + "<label class=fic-c><span>Observação</span>"
        + f"<textarea name=obs rows=3>{esc(d.get('obs') or '')}</textarea></label>")

    # O <form> é o próprio item flex do shell: assim o miolo (.scroll) rola e o botão
    # (.rodape-b) fica preso embaixo, sem precisar do atributo form= no botão.
    corpo = (_hdr("Ficha do cliente", d["empresa"], voltar=f"{_BASE}/lead/{lead_id}")
             + _flash(request)
             + f"<form class=telaform method=post action='{_BASE}/lead/{lead_id}/ficha'>"
             + f"<div class=scroll><div class=secao><div class='fic'>{campos}</div>"
             + "<div class=fonte>Campo em branco não apaga o que já está salvo — dá pra "
               "voltar aqui e ir completando conforme a conversa anda.</div></div></div>"
             + "<div class=rodape-b><button class=btn type=submit>Salvar ficha</button></div>"
             + "</form>" + _CEP_JS)
    return _page(f"Ficha — {d['empresa']}", corpo)


# ------------------------------------------------------------------ lead novo (manual)
@router.get("/cockpit/lead/novo", response_class=HTMLResponse)
def cockpit_lead_novo_tela(request: Request):
    """Cadastrar um lead na mão, do celular.

    Faltava: o app tinha vinte e tantas rotas e nenhuma criava lead — o vendedor só
    trabalhava o que o rodízio entregava, e o contato pego na rua não tinha onde entrar.

    Dois campos e pronto. O resto (documento, CEP, endereço, aniversário) ele completa
    depois na ficha, que já tem tudo: formulário longo no celular, em pé na calçada, é
    o jeito mais certo de o lead não ser cadastrado."""
    sess = _sessao(request)
    if not sess:
        return RedirectResponse("/cockpit/login", status_code=303)
    corpo = (_hdr("Novo lead", "entra na sua fila", voltar=_BASE)
             + _flash(request)
             + f"<form class=telaform method=post action='{_BASE}/lead/novo'>"
             + "<div class=scroll><div class=secao><div class='fic'>"
             + "<label class=fic-c><span>Nome do contato</span>"
               "<input name=nome required autocomplete=off autofocus"
               " placeholder='Como você vai reconhecer'></label>"
             + "<label class=fic-c><span>WhatsApp</span>"
               "<input name=whatsapp required type=tel inputmode=tel autocomplete=off"
               " placeholder='DDD + número'></label>"
             + "</div><div class=fonte>Já tem esse número na base? A gente abre o lead "
               "que existe em vez de criar outro — conversa partida em duas fichas é pior "
               "que lead repetido.</div></div></div>"
             + "<div class=rodape-b><button class=btn type=submit>Criar e abrir</button></div>"
             + "</form>")
    return _page("Novo lead", corpo)


@router.post("/cockpit/lead/novo")
def cockpit_lead_novo(request: Request, nome: str = Form(""), whatsapp: str = Form("")):
    sess = _sessao(request)
    if not sess:
        return RedirectResponse("/cockpit/login", status_code=303)
    conta_id, membro_id = sess
    r = ck.criar_lead(get_pool(), conta_id, membro_id, nome, whatsapp)
    if not r.get("ok"):
        request.session["ck_err"] = _erro(r)
        return RedirectResponse(f"{_BASE}/lead/novo", status_code=303)
    # `existia` não é erro — é o caso normal de quem não sabe de cor quem já está na
    # base. Diz o que aconteceu e abre o lead certo, em vez de deixar a pessoa achando
    # que criou um novo.
    request.session["ck_ok"] = ("Esse número já era um lead — abrindo ele."
                                if r.get("existia") else "Lead criado ✓")
    return RedirectResponse(f"{_BASE}/lead/{r['lead_id']}", status_code=303)


# ------------------------------------------------------------------ propostas
_STATUS_SEG = [("", "Todas"), ("enviado", "Enviadas"), ("negociando", "Negociando"),
               ("aprovada", "Aprovadas"), ("fechado", "Fechadas"), ("perdido", "Perdidas")]
_STATUS_CLS = {"aprovada": "neon", "fechado": "neon", "perdido": "err",
               "negociando": "voce", "enviado": "", "rascunho": ""}


@router.get("/cockpit/orcamentos", response_class=HTMLResponse)
def cockpit_orcamentos(request: Request, s: str = "", v: str = ""):
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
        # A linha só diz o que ela sabe. Rascunho sem item montado vale zero de
        # verdade, mas escrever "R$ 0" anuncia um preço que ninguém fez; e o "—"
        # do autor é o coalesce de cockpit.orcamentos pra proposta cujo criado_por
        # não casa com membro nenhum — resposta certa da consulta, travessão solto
        # na tela. Sem os dois, a carteira para de parecer uma lista de zeros.
        cents = o["setup_centavos"] + o["mensal_centavos"]
        val = _brl(cents) if cents else "sem valor ainda"
        sub = o["vendedor"] if gestao else _data(o["criado_em"])
        if sub == "—":
            sub = ""
        linhas.append(
            f"<a class=lead href='{_BASE}/orcamentos/{o['id']}'>"
            f"<span class=mid><span class=top><span class=emp>{esc(o['titulo'])}</span>"
            f"<span class='chip {_STATUS_CLS.get(o['status'], '')}'>{esc(o['status_rot'])}</span></span>"
            f"<span class=snip>{esc(val)}" + (f" · {esc(sub)}" if sub else "")
            + "</span></span></a>")
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
        corpo = (
                   _hdr_dono(conta_id, "Propostas", "a carteira do time")
                 + _flash(request) + filtros
                 + f"<div class=scroll>{miolo}</div>" + _abas_dono("orcamentos"))
    else:
        p = ck.perfil(pool, conta_id, sess[1])
        corpo = (
                   _hdr("Minhas propostas", "manda pro cliente por aqui",
                        inicial=_ini(p["nome"]))
                 + _flash(request) + filtros
                 + f"<div class=scroll>{miolo}</div>"
                 + _abas_vend("orcamentos", 0 if gestao else _pend_vend(conta_id, sess[1])))
    return _page("Propostas", corpo)


@router.get("/cockpit/orcamentos/{orc_id}", response_class=HTMLResponse)
def cockpit_orcamento(request: Request, orc_id: int):
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
    # O e-mail é a saída que NÃO tira o vendedor do Zaq: sai pelo SMTP do sistema,
    # assinado pela empresa, e volta pra esta tela com o resultado. Fica em primeiro
    # porque é o caminho que a gente quer ensinar.
    envio = []
    if not gestao:
        tem_email = "@" in (o.get("email") or "")
        if tem_email:
            envio.append(f"<form method=post action='{_BASE}/orcamentos/{orc_id}/email'>"
                         "<button class=btn type=submit>Enviar por e-mail</button></form>"
                         f"<div class=dica>para <b>{esc(o['email'])}</b></div>")
        else:
            # sem e-mail o botão não some sem explicação: some dizendo o que falta,
            # senão o vendedor fica olhando pra uma tela que mudou e não sabe por quê
            envio.append("<div class=dica style='margin:0 0 .5rem'>Esse cliente não tem "
                         "e-mail cadastrado — dá pra mandar na conversa ou copiar o link.</div>")
    if o["zap"]:
        envio.append(f"<a class='btn ghost' style='margin-top:.5rem' href='{esc(o['zap'])}' "
                     f"target=_blank rel=noopener>{_ic('zap', 'ic p')} Mandar no WhatsApp</a>")
    if o["lead_id"] and not gestao:
        envio.append(f"<form method=post action='{_BASE}/orcamentos/{orc_id}/enviar'>"
                     "<button class='btn ghost' style='margin-top:.5rem' type=submit>"
                     "Enviar na conversa do lead</button></form>")
    if o["link"]:
        envio.append(f"<a class='btn ghost' style='margin-top:.5rem' href='{esc(o['link'])}' "
                     f"target=_blank rel=noopener>Abrir a proposta como o cliente vê</a>")
        # o fetch anota que a proposta saiu por aqui — é o que faz o card do funil
        # andar sozinho. `keepalive` porque quem copia troca de app em seguida, e o
        # `catch` é mudo de propósito: o link JÁ está na área de transferência, e um
        # erro de rede não pode virar aviso dizendo o contrário.
        envio.append("<div class=copiar><input value='" + esc(o["link"]) + "' readonly "
                     "onclick='this.select()'><button type=button onclick=\"navigator.clipboard"
                     ".writeText(this.previousElementSibling.value);this.textContent='Copiado';"
                     "fetch('" + _BASE + "/orcamentos/" + str(orc_id) + "/link-copiado',"
                     "{method:'POST',keepalive:true}).catch(function(){})\">"
                     "Copiar</button></div>")

    fechada = o["status"] == "fechado"

    def opt(k, lab):
        return f"<option value='{k}'{' selected' if o['status'] == k else ''}>{esc(lab)}</option>"
    if fechada:
        # já virou contrato: os títulos existem, reabrir geraria o contrato em dobro
        mover = ("<div class=eyebrow>Funil</div><div class=bloco><div class=card "
                 "style='font-size:.84rem;color:var(--text-dim)'>Esta proposta já virou "
                 "<b style='color:var(--neon)'>contrato</b> — os títulos a receber estão no "
                 "módulo Empresa. Pra mexer no valor agora, é por lá.</div></div>")
    else:
        mover = ("<div class=eyebrow>Mover no funil</div><div class=bloco>"
                 f"<form method=post action='{_BASE}/orcamentos/{orc_id}/status' class=linhaform>"
                 "<select name=status>"
                 + "".join(opt(k, ck._ROT_ORC.get(k, k.title())) for k in ck.STATUS_MANUAIS)
                 + "</select><button class=btn style='width:auto;padding:.55rem 1rem' type=submit>"
                 "Salvar</button></form></div>")

    # Fechar contrato é a única ação daqui que mexe em dinheiro: gera os títulos a
    # receber. Por isso vive atrás de dois toques e diz o que vai acontecer ANTES —
    # não é um botão que se aperta sem querer rolando a tela.
    if fechada:
        fechar = ""
    else:
        recorrente = (f" e <b>{esc(_brl(o['mensal_centavos']))}/mês</b> recorrente"
                      if o["mensal_centavos"] else "")
        entrada = (f"<b>{esc(_brl(o['setup_centavos']))}</b> de entrada (vence em 7 dias)"
                   if o["setup_centavos"] else "o valor da proposta")
        fechar = ("<div class=eyebrow>Fechar</div><div class=bloco>"
                  "<details class=fechar><summary>Fechar contrato</summary>"
                  f"<p>Vira contrato e cria os títulos a receber: {entrada}{recorrente}. "
                  "Eles aparecem em <b>Empresa → a receber</b>.</p>"
                  "<p class=aviso-p>Depois de fechar, a proposta não volta atrás por aqui.</p>"
                  f"<form method=post action='{_BASE}/orcamentos/{orc_id}/fechar'>"
                  "<button class=btn type=submit>Confirmar e gerar os títulos</button></form>"
                  "</details></div>")

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
    corpo = (
               _hdr(o["titulo"], o["status_rot"], voltar=f"{_BASE}/orcamentos")
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
             + mover + fechar + editar
             + (f"<div class=eyebrow>O que entra</div><div class=bloco><div class=card>{itens}</div></div>"
                if itens else "")
             + (f"<div class=eyebrow>Cliente</div><div class=bloco><div class=card>{ficha_html}</div></div>"
                if ficha_html else "")
             + "</div>"
             + (_abas_dono("orcamentos") if gestao
                else _abas_vend("orcamentos", _pend_vend(conta_id, sess[1]))))
    return _page(o["titulo"], corpo)


@router.post("/cockpit/orcamentos/{orc_id}/status")
def cockpit_orcamento_status(request: Request, orc_id: int, status: str = Form(...)):
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


@router.post("/cockpit/orcamentos/{orc_id}/fechar")
def cockpit_orcamento_fechar(request: Request, orc_id: int):
    """Fecha a proposta como contrato. Mesmo motor do botão do painel
    (vendas.fechar_orcamento, via ck.fechar_contrato) — inclusive a trava de
    idempotência, que impede o duplo-clique gerar título em dobro."""
    g = _gerencia(request)
    sess = _sessao(request)
    if not g and not sess:
        return RedirectResponse("/cockpit/login", status_code=303)
    conta_id = g[0] if g else sess[0]
    r = ck.fechar_contrato(get_pool(), conta_id, orc_id,
                           membro_id=None if g else sess[1])
    request.session["ck_ok" if r.get("ok") else "ck_err"] = (
        r.get("msg", "Contrato fechado ✓") if r.get("ok") else r.get("erro", "Não deu certo."))
    return RedirectResponse(f"{_BASE}/orcamentos/{orc_id}", status_code=303)


@router.post("/cockpit/orcamentos/{orc_id}/link-copiado")
def cockpit_orcamento_link_copiado(request: Request, orc_id: int):
    """Anota que o vendedor pegou o link desta proposta pra mandar pro cliente.

    Mesmo motivo da rota irmã no painel: no app o vendedor copia o link e cola no
    WhatsApp dele. Sem esta anotação o Zaq não sabe que a proposta saiu, e o card
    fica parado enquanto o cliente já está com ela na mão.

    Chama-se "link copiado", e não "enviado": aqui o Zaq não entregou nada — só sabe
    que o link saiu da tela.

    `ck.orcamento` antes de anotar não é enfeite: ele é quem confere que a proposta
    é DESTA conta (e deste vendedor). Sem essa volta, um id chutado na barra de
    endereço anotaria envio na proposta de outra empresa."""
    sess = _sessao(request)
    if not sess:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    conta_id, membro_id = sess
    try:
        from finance import proposta_email as _pe
        if ck.orcamento(get_pool(), conta_id, orc_id, membro_id=membro_id):
            _pe.registrar(get_pool(), conta_id, orc_id, destino="", remetente_usado="",
                          ok=True, canal="link", por=str(membro_id or ""))
    except Exception:  # noqa: BLE001 — anotação não derruba a tela
        _logging.getLogger("cockpit.envio").warning(
            "proposta %s: não registrei o link copiado", orc_id, exc_info=True)
    return JSONResponse({"ok": True})


@router.post("/cockpit/orcamentos/{orc_id}/enviar")
def cockpit_orcamento_enviar(request: Request, orc_id: int):
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


@router.post("/cockpit/orcamentos/{orc_id}/email")
def cockpit_orcamento_email(request: Request, orc_id: int):
    """Manda o link da proposta pro e-mail do cliente, assinado pela empresa."""
    sess = _sessao(request)
    if not sess:
        return RedirectResponse("/cockpit/login", status_code=303)
    conta_id, membro_id = sess
    r = ck.enviar_proposta_email(get_pool(), conta_id, orc_id, membro_id=membro_id)
    request.session["ck_ok" if r.get("ok") else "ck_err"] = (
        f"Proposta enviada para {r.get('destino','')} ✓" if r.get("ok")
        else r.get("erro", "Não consegui enviar."))
    return RedirectResponse(f"{_BASE}/orcamentos/{orc_id}", status_code=303)


@router.get("/cockpit/perfil", response_class=HTMLResponse)
def cockpit_perfil(request: Request):
    """Perfil do vendedor — e, pro dono/gestor, o perfil que a versão anterior não tinha.

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

    corpo = (
               _hdr("Meu perfil", "vendedor")
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
             # o login do vendedor agora cai no Cockpit; sem esta porta ele não
             # chegaria no painel a não ser digitando a URL. /painel resolve
             # sozinho — o gate afunila pra área do papel e da conta.
             + "<div class=bloco><a class='btn ghost' style='margin-bottom:.5rem' "
               "href='/painel'>Abrir o painel completo</a>"
             + f"<a class='btn ghost' href='/cockpit/sair'>{_ic('sair', 'ic p')} Sair</a></div>"
             + "</div>" + _abas_vend("perfil", _pend_vend(conta_id, membro_id)))
    return _page("Meu perfil", corpo)


def _perfil_dono(conta_id: int, membro_id: int | None) -> HTMLResponse:
    marca = _marca_conta(conta_id)
    # o dono titular não é membro da equipe, então não tem fila própria — só o
    # gestor (que também vende) tem caixa pra abrir
    minha_caixa = (f"<a class='btn ghost' href='{_BASE}?meus=1'>Ver a minha caixa de leads</a>"
                   if membro_id else "")
    corpo = (
               # o mesmo topo das outras telas de gestão (ver _hdr_dono): sem o
               # avatar, esta era a única tela do gestor sem nada no canto esquerdo
               _hdr("Perfil", "dono · gestão da equipe",
                    inicial=marca["iniciais"], href_inicial=f"{_BASE}/perfil")
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
_VOZ_JS = r"""
<script>
(function(){
  var BASE="__BASE__", LEAD=__LEAD__, TETO=90;
  function $(id){return document.getElementById(id);}
  var comp=$("comp"), grav=$("grav"), rel=$("rel"), dica=$("dica"), nivel=$("nivel");
  var mr=null, pedacos=[], t0=0, tick=null, cancelado=false, enviando=false;
  var fluxo=null, ctx=null, an=null, medir=null, niveis=[], tipoAtual="";

  // ---- estado da barra. TUDO que o dedo toca muda a tela ANTES do trabalho ----
  // O que fazia parecer travado: a barra só aparecia depois do getUserMedia (0,3
  // a 2s no celular) e o "enviando" só depois do mr.stop() resolver. Agora o
  // estado troca no próprio toque, e o assíncrono corre por baixo.
  function estado(e, texto){
    grav.classList.remove("st-prep","st-grav","st-env");
    grav.classList.add("st-" + e);
    if(texto) dica.textContent = texto;
  }
  function abrir(){ comp.classList.add("gravando-on"); grav.classList.add("on"); }
  function sair(){
    clearInterval(tick); tick=null;
    cancelAnimationFrame(medir); medir=null;
    comp.classList.remove("gravando-on"); grav.classList.remove("on");
    if(fluxo){ fluxo.getTracks().forEach(function(t){t.stop();}); fluxo=null; }
    if(ctx){ try{ ctx.close(); }catch(e){} ctx=null; an=null; }
    mr=null; pedacos=[]; niveis=[]; enviando=false;
    $("manda").disabled=false; $("cancela").disabled=false;
  }
  function mmss(s){return Math.floor(s/60)+":"+("0"+Math.floor(s%60)).slice(-2);}

  function tipoBom(){
    var opcoes=["audio/webm;codecs=opus","audio/webm","audio/mp4"];
    for(var i=0;i<opcoes.length;i++){
      if(window.MediaRecorder && MediaRecorder.isTypeSupported(opcoes[i])) return opcoes[i];
    }
    return "";
  }

  // A ONDA SAI DA GRAVAÇÃO, não de decodificar depois. Decodificar o áudio
  // inteiro acontecia DEPOIS de a pessoa apertar enviar — quanto mais longo o
  // áudio, mais tempo de tela parada. Aqui a amplitude é lida ao vivo, o que de
  // quebra alimenta o medidor: o vendedor VÊ que o microfone está ouvindo.
  function ouvir(st){
    var Ctx=window.AudioContext||window.webkitAudioContext;
    if(!Ctx) return;
    try{
      ctx=new Ctx();
      if(ctx.state==="suspended") ctx.resume();   // iOS sobe suspenso
      an=ctx.createAnalyser(); an.fftSize=512;
      ctx.createMediaStreamSource(st).connect(an);
      var buf=new Uint8Array(an.fftSize);
      (function passo(){
        if(!an) return;
        an.getByteTimeDomainData(buf);
        var soma=0;
        for(var i=0;i<buf.length;i++){ var v=(buf[i]-128)/128; soma+=Math.abs(v); }
        var m=soma/buf.length;
        niveis.push(m);
        // 2% de piso: barra zerada parece desligada, e microfone mudo não é isso
        nivel.style.width=Math.max(2,Math.min(100,Math.round(m*380)))+"%";
        medir=requestAnimationFrame(passo);
      })();
    }catch(e){ ctx=null; an=null; }
  }
  // 64 baldes, média por balde, normalizado pelo pico — a mesma forma que o
  // Baileys calcula decodificando. Não é byte a byte igual (dois decodificadores
  // já divergem entre si), e numa barrinha decorativa isso não se vê.
  function ondaPronta(){
    // menos de 4 leituras é áudio de um piscar de olhos: não há forma pra desenhar
    if(niveis.length < 4) return null;
    var n=64, bloco=niveis.length/n, f=[];
    for(var i=0;i<n;i++){
      var ini=Math.floor(i*bloco), fim=Math.floor((i+1)*bloco), soma=0, c=0;
      for(var j=ini;j<fim;j++){ soma+=niveis[j]; c++; }
      // gravação curta tem menos leituras que baldes: cada balde pega a leitura
      // mais próxima em vez de sair vazio — senão um áudio de meio segundo ia
      // sem onda nenhuma e a bolha saía chapada.
      f.push(c ? soma/c : niveis[Math.min(niveis.length-1, ini)]);
    }
    var pico=Math.max.apply(null,f);
    var w=new Uint8Array(n);
    for(var k=0;k<n;k++) w[k]=pico>0?Math.floor(100*f[k]/pico):0;
    return w;
  }
  function b64(u8){ var s2=""; for(var i=0;i<u8.length;i++) s2+=String.fromCharCode(u8[i]); return btoa(s2); }

  $("mic").onclick=function(){
    var tipo=tipoBom();
    if(!tipo){ alert("Este navegador não grava áudio. Responda por texto."); return; }
    tipoAtual=tipo;
    cancelado=false; enviando=false; pedacos=[]; niveis=[];
    abrir(); estado("prep","preparando…");      // ← a tela responde AQUI
    navigator.mediaDevices.getUserMedia({audio:true}).then(function(st){
      if(cancelado){ st.getTracks().forEach(function(t){t.stop();}); sair(); return; }
      fluxo=st;
      // 24 kbps é taxa de voz: 27s (a média do vendedor) dão ~66 KB. O padrão do
      // navegador é ~118 kbps, cinco vezes mais, sem ganho nenhum pra fala.
      try{ mr=new MediaRecorder(st,{mimeType:tipo,audioBitsPerSecond:24000}); }
      catch(e){ mr=new MediaRecorder(st,{mimeType:tipo}); }
      mr.ondataavailable=function(e){ if(e.data && e.data.size) pedacos.push(e.data); };
      mr.onstop=function(){ if(cancelado){ sair(); return; } mandar(); };
      mr.start();
      ouvir(st);
      t0=Date.now();
      rel.textContent="0:00"; estado("grav");
      tick=setInterval(function(){
        var s3=(Date.now()-t0)/1000;
        rel.textContent=mmss(s3);
        if(s3>=TETO) pedirEnvio();
      },200);
    }).catch(function(){
      sair();
      alert("Não consegui acessar o microfone. Confira a permissão do navegador.");
    });
  };

  // ---- os dois botões respondem no toque, e só depois param o gravador ----
  function pedirEnvio(){
    if(enviando) return;
    enviando=true;
    estado("env","enviando…");                  // ← a tela responde AQUI
    cancelAnimationFrame(medir); medir=null;
    clearInterval(tick); tick=null;
    try{ if(mr && mr.state!=="inactive") mr.stop(); else mandar(); }
    catch(e){ sair(); }
  }
  $("manda").onclick=pedirEnvio;
  $("cancela").onclick=function(){
    cancelado=true;
    estado("env","cancelando…");                // ← e AQUI
    try{ if(mr && mr.state!=="inactive") mr.stop(); else sair(); }
    catch(e){ sair(); }
  };

  function mandar(){
    var seg=Math.max(1,Math.round((Date.now()-t0)/1000));
    var blob=new Blob(pedacos,{type:tipoAtual});
    var onda=ondaPronta();                      // já está pronta: zero espera
    if(fluxo){ fluxo.getTracks().forEach(function(t){t.stop();}); fluxo=null; }
    if(ctx){ try{ ctx.close(); }catch(e){} ctx=null; an=null; }
    blob.arrayBuffer().then(function(buf){
      var h={"Content-Type": blob.type || tipoAtual};
      if(onda) h["X-Onda"]=b64(onda);
      return fetch(BASE+"/lead/"+LEAD+"/audio?seg="+seg,{method:"POST",headers:h,body:buf});
    }).then(function(r){ return r.json(); }).then(function(j){
      if(j && j.ok){
        sair();
        // a conversa já se atualiza sozinha (puxa): recarregar a página inteira
        // custava ~1s de tela branca logo depois de enviar
        if(window.__puxa) window.__puxa(); else location.reload();
        return;
      }
      alert((j && j.erro) || "Não consegui enviar o áudio.");
      sair();
    }).catch(function(){
      alert("Falha de conexão ao enviar o áudio.");
      sair();
    });
  }
})();
</script>"""


@router.get("/cockpit/lead/{lead_id}", response_class=HTMLResponse)
def cockpit_lead(request: Request, lead_id: int):
    """Uma rota, dois papéis. Se o lead é do vendedor logado, abre a tela de trabalho
    (chat em primeiro plano). Se não é dele mas quem olha é gestão, abre a visão de
    gestão — em vez de chutar pro /painel/prospeccao, como a versão anterior fazia."""
    sess = _sessao(request)
    if sess:
        # pos_visto: abrir a conversa põe o vendedor em dia, então o cooldown do push
        # zera e a próxima mensagem do cliente toca na hora (ver lead_do_vendedor).
        d = ck.lead_do_vendedor(get_pool(), sess[0], sess[1], lead_id, pos_visto=True)
        if d:
            _entrega = ck.entrega_sempre(get_pool(), sess[0])
            return _lead_vendedor(request, lead_id, d,
                                  pode_voz=ck.pode_gravar_audio(get_pool(), sess[0]),
                                  saida_wa=not _entrega)
    g = _gerencia(request)
    if g:
        return _lead_gestor(request, g[0], lead_id,
                            saida_wa=not ck.entrega_sempre(get_pool(), g[0]))
    return RedirectResponse("/cockpit/login", status_code=303)


def _lead_vendedor(request: Request, lead_id: int, d: dict,
                   pode_voz: bool = False, saida_wa: bool = True) -> HTMLResponse:
    sub = " · ".join(x for x in [d.get("cidade") or "", d.get("uf") or ""] if x) or (d.get("doc_fmt") or "")

    bolhas = []
    for m in d["mensagens"]:
        who = m["who"]
        rot = ("<div class=who>Agente</div>" if who == "ia"
               else "<div class=who>Você</div>" if who == "out" else "")
        bolhas.append(f"<div class='bub {esc(who)}' data-id='{m.get('id') or 0}'>"
                      f"{rot}{esc(m['texto'])}</div>")
    if d["ia"]:
        bolhas.insert(0, "<div class=aviso>O agente está atendendo. Toque em "
                         "<b>Assumir</b> pra responder você.</div>")
    chat = "".join(bolhas) or "<div class=aviso>Sem mensagens ainda.</div>"

    if d["ia"]:
        acao = (f"<form method=post action='{_BASE}/lead/{lead_id}/assumir'>"
                "<button class=btn type=submit>Assumir a conversa</button></form>")
    else:
        # `pode_voz` chega de fora (o microfone só existe no canal QR): quem
        # monta a tela não vai buscar sessão nem banco pra decidir isso.
        mic = ("<button type=button class=mic id=mic aria-label='Gravar áudio'>"
               "<svg width=20 height=20 viewBox='0 0 24 24' fill=none stroke=currentColor "
               "stroke-width=1.8 stroke-linecap=round><rect x=9 y=3 width=6 height=11 rx=3/>"
               "<path d='M5 11a7 7 0 0014 0M12 18v3'/></svg></button>") if pode_voz else ""
        # a barra de gravação também é gateada: sem microfone ela seria marcação
        # morta em toda tela de conversa de todo vendedor
        barra = ("<div class=gravando id=grav>"
                 "<span class=bolha></span><span class=rel id=rel>0:00</span>"
                 "<span class=nivel><i id=nivel></i></span>"
                 "<span class=dica id=dica>preparando…</span>"
                 "<button type=button class=cancela id=cancela>Cancelar</button>"
                 "<button type=button class=manda id=manda aria-label='Enviar áudio'>&#10148;</button>"
                 "</div>") if pode_voz else ""
        acao = (f"<form class=composer id=comp method=post action='{_BASE}/lead/{lead_id}/mensagem'>"
                "<input name=texto placeholder='Responder…' required autocomplete=off>"
                + mic +
                "<button type=submit aria-label=Enviar>&#10148;</button>"
                + barra + "</form>")
        if pode_voz:
            acao += _VOZ_JS.replace("__BASE__", _BASE).replace("__LEAD__", str(lead_id))

    # Quatro atalhos, e não cinco: o `.grade` é de DUAS colunas, então o quinto nascia
    # sozinho na terceira linha, meia largura, com um buraco do lado. Saiu o "Ligar" —
    # quem chega por `whatsapp_inbound` tem o número em `whatsapp` e `telefone` NULL, e
    # o botão vinha CINZA (tel_link só olha `telefone`) na maioria dos leads deste app;
    # um atalho apagado ocupando o melhor lugar da tela não vale a linha extra. A Ficha
    # sobe pro lugar dele, e as linhas ficam com sentido próprio: em cima o contato e os
    # dados, embaixo as duas ações coloridas.
    # A PORTA PRO CELULAR. Onde o Zaq entrega sempre (canal QR) ela não existe: o
    # atalho abria o wa.me e era o próprio app convidando o vendedor a sair dele —
    # e o que sai por fora chega sem nome (98% do que a Prime mandava). Onde o Zaq
    # NÃO entrega sempre (janela de 24h da API oficial) o atalho fica, senão o
    # vendedor ficaria sem resposta num horário morto.
    zap = (d.get("zap_link") or "") if saida_wa else ""
    atalhos = (
        ((f"<a href='{esc(zap)}' target=_blank rel=noopener>{_ic('zap', 'ic p')} WhatsApp</a>" if zap
          else f"<span class=off>{_ic('zap', 'ic p')} WhatsApp</span>") if saida_wa else "")
        + f"<a href='{_BASE}/lead/{lead_id}/ficha'>{_ic('ficha', 'ic p')} Ficha</a>"
        + f"<a class=orc href='{_BASE}/lead/{lead_id}/orcamento'>{_ic('orc', 'ic p')} Orçamento</a>"
        + f"<a class=vis2 href='{_BASE}/lead/{lead_id}/visita'>{_ic('agenda', 'ic p')} Visita</a>")

    etapas = "".join(
        f"<form method=post action='{_BASE}/lead/{lead_id}/etapa'>"
        f"<input type=hidden name=etapa value='{esc(e['chave'])}'>"
        f"<button class='{'on' if d['status'] == e['chave'] else ''}' type=submit>{esc(e['rotulo'])}</button>"
        "</form>" for e in d["etapas"])

    motivos = "".join(f"<option>{esc(m)}</option>" for m in
                      ("Preço", "Sem retorno", "Comprou concorrente", "Fora do perfil", "Sem interesse"))

    # A folha só sobe com :target — sem JS, então funciona igual ao resto do app,
    # que é todo form + redirect. Ela só se sustenta CURTA: é `position:absolute` com
    # `max-height:84%`, e enquanto o conteúdo cabe na caixa ela não rola por dentro.
    # A ficha morou aqui por um PR e custou caro — 548px de formulário num conteúdo
    # de 410px fizeram a rolagem interna nascer, e no Safari o salto de âncora sobre
    # um container absoluto que passou a rolar abria a folha no FIM do conteúdo, com
    # um vazio embaixo. Formulário tem tela própria (como orçamento e visita); aqui
    # só entra o que é curto.
    folha = (
        "<div class=folha id=acoes><div class=puxa></div>"
        f"<div class=grade>{atalhos}</div>"
        + f"<h3>Etapa no funil</h3><div class=etapas>{etapas}</div>"
        + f"<h3>Fechar</h3>"
        f"<form method=post action='{_BASE}/lead/{lead_id}/fechar' style='margin-bottom:.5rem'>"
        "<input type=hidden name=tipo value=ganho><button class=btn type=submit>Marcar como ganho</button></form>"
        f"<form method=post action='{_BASE}/lead/{lead_id}/fechar' class=linhaform>"
        "<input type=hidden name=tipo value=perdido>"
        f"<select name=motivo><option value=''>Motivo (opcional)</option>{motivos}</select>"
        "<button class='btn perigo' style='width:auto;padding:.55rem .9rem' type=submit>Perdido</button></form>"
        + "</div><a class=fbg href='#fechar' aria-label='Fechar'></a>")

    # Conversa abre no fim, na mensagem mais recente — é onde o trabalho está.
    # Sem 'smooth' de propósito: tem que JÁ NASCER embaixo, e não descer na frente
    # do vendedor. Duas tentativas porque a primeira roda no meio do parse, quando
    # o .chat (flex:1) ainda não sabe a altura dele; a segunda garante. Como a tela
    # é form + redirect, isso também cobre o depois de enviar. Mesma ideia do
    # cxScroll do inbox do gestor (web/painel_prospeccao.py).
    #
    # E daí em diante ela se atualiza sozinha: o vendedor deixava a conversa aberta e
    # não via a resposta do cliente chegar — tinha que recarregar pra descobrir.
    fim = ("<script>(function(){"
           "var chat=document.querySelector('.chat');if(!chat)return;"
           "function fim(){chat.scrollTop=chat.scrollHeight;}"
           "fim();document.addEventListener('DOMContentLoaded',fim);"
           f"var ultimo={d['mensagens'][-1].get('id') or 0 if d['mensagens'] else 0},"
           f"ia={'true' if d['ia'] else 'false'},ocupado=false;"
           "function rot(w){return w==='ia'?'<div class=who>Agente</div>':"
           "w==='out'?'<div class=who>Você</div>':'';}"
           "function txt(s){var e=document.createElement('div');e.textContent=s;return e.innerHTML;}"
           "function puxa(){"
           "if(ocupado||document.visibilityState!=='visible')return;ocupado=true;"
           f"fetch('{_BASE}/lead/{lead_id}/mensagens?desde='+ultimo)"
           ".then(function(r){return r.json();}).then(function(j){"
           "ocupado=false;if(!j||!j.ok)return;"
           # quem responde mudou (o agente assumiu, ou um colega assumiu por você):
           # o composer inteiro é outro, então recarregar é mais honesto que remendar
           "if(j.ia!==ia){location.reload();return;}"
           "if(!j.msgs||!j.msgs.length)return;"
           # a regra que o inbox do gestor já acertou: no rodapé, segue a conversa;
           # se o vendedor subiu pra ler o histórico, a posição dele é preservada
           "var perto=(chat.scrollHeight-chat.scrollTop-chat.clientHeight)<80;"
           "j.msgs.forEach(function(m){"
           "var d=document.createElement('div');d.className='bub '+m.who;"
           "d.setAttribute('data-id',m.id);d.innerHTML=rot(m.who)+txt(m.texto);"
           "chat.appendChild(d);ultimo=m.id;});"
           "if(perto)fim();"
           "}).catch(function(){ocupado=false;});}"
           # exposto pra quem manda áudio puxar na hora: recarregar a página inteira
           # depois de enviar dá ~1s de tela branca num aparelho de vendedor
           "window.__puxa=puxa;setInterval(puxa,8000);"
           "document.addEventListener('visibilitychange',function(){"
           "if(document.visibilityState==='visible')puxa();});"
           "})();</script>")

    chip = ("<span class='chip ia'>IA</span>" if d["ia"] else "<span class='chip voce'>você</span>")
    corpo = (
               _hdr(d["empresa"], sub, voltar=_BASE, direita=chip)
             + _flash(request)
             + f"<div class=chat>{chat}</div>"
             + f"<div class=rodape>{acao}"
             + "<a class='btn ghost' style='margin-top:.5rem' href='#acoes'>Ficha, funil e fechamento</a>"
             + "</div>" + folha + fim)
    return _page(d["empresa"], corpo)


def _lead_gestor(request: Request, conta_id: int, lead_id: int, saida_wa: bool = True) -> HTMLResponse:
    """A tela que não existia. Na versão anterior o gestor tocava num lead da equipe e caía em
    /painel/prospeccao/{id} — o painel desktop, no meio do celular."""
    from web.painel_prospeccao import _carrega_alvo
    pool = get_pool()
    d = _carrega_alvo(pool, conta_id, lead_id)
    if not d:
        return RedirectResponse(f"{_BASE}/equipe/leads", status_code=303)
    outros = cd.vendedores_para_reatribuir(pool, conta_id, d.get("vendedor_id") or 0)

    sub = " · ".join(x for x in [d.get("cidade") or "", d.get("uf") or ""] if x) or (d.get("doc_fmt") or "")
    # mesma porta da tela do vendedor, mesmo portão: onde o Zaq entrega sempre, a
    # conversa fica por dentro — inclusive pra quem gerencia (o que o gestor manda
    # do celular chega tão sem nome quanto o do vendedor)
    tel = d.get("tel_link") or ""
    zap = (d.get("zap_link") or "") if saida_wa else ""
    atalhos = (
        (f"<a href='{esc(tel)}'>{_ic('ligar', 'ic p')} Ligar</a>" if tel
         else f"<span class=off>{_ic('ligar', 'ic p')} Ligar</span>")
        + ((f"<a href='{esc(zap)}' target=_blank rel=noopener>{_ic('zap', 'ic p')} WhatsApp</a>" if zap
            else f"<span class=off>{_ic('zap', 'ic p')} WhatsApp</span>") if saida_wa else "")
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

    corpo = (
               _hdr(d.get("empresa") or "Lead", sub, voltar=f"{_BASE}/equipe/leads")
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
    corpo = (
               _hdr_dono(conta_id, "Equipe", "acompanhe o time")
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


@router.get("/cockpit/equipe/placar", response_class=HTMLResponse)
def cockpit_placar(request: Request):
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

    corpo = (
               _hdr_dono(g[0], "Placar", "este mês, por R$ fechado")
             + f"<div class=scroll>{miolo}</div>" + _abas_dono("placar"))
    return _page("Placar", corpo)


@router.get("/cockpit/equipe/atividade", response_class=HTMLResponse)
def cockpit_atividade(request: Request):
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
    corpo = (
               _hdr_dono(g[0], "Atividade", "o que o time fez")
             + f"<div class=scroll>{linhas}</div>" + _abas_dono("ativ"))
    return _page("Atividade", corpo)


_ETAPA_ROT = {"novo": "Novo", "contatado": "Contatado", "qualificado": "Qualificado",
              "proposta": "Proposta"}
_TEMP_ROT = [("quente", "Quente"), ("morno", "Morno"), ("frio", "Frio")]


@router.get("/cockpit/equipe/leads", response_class=HTMLResponse)
def cockpit_leads(request: Request, vend: str = "", etapa: str = "", temp: str = ""):
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

    corpo = (
               _hdr_dono(g[0], "Leads da equipe", "todos os leads abertos")
             + filtros + f"<div class=scroll>{miolo}</div>" + _abas_dono("leads"))
    return _page("Leads da equipe", corpo)


@router.get("/cockpit/equipe/vendedor/{membro_id}", response_class=HTMLResponse)
def cockpit_vendedor(request: Request, membro_id: int):
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

    corpo = (
               _hdr(v["nome"], v["papel"], voltar=f"{_BASE}/equipe/placar")
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
# Nenhuma escrita mora aqui: cada rota chama a função correspondente de
# finance/cockpit.py ou finance/cockpit_dono.py — e é lá, no motor, que a posse
# do lead é revalidada no banco a cada ação. Nunca confie no que chegou pela URL.
#
# O motor devolve alguns erros como código pra máquina ler ('escopo', 'vazio'),
# misturados com frases prontas. Sem esta tabela o vendedor lia "escopo" na tela —
# era o que acontecia antes. Frase que já vem pronta passa direto.
_RECADO = {
    "escopo": "Esse lead não é seu.",
    "vazio": "Escreva alguma coisa antes de enviar.",
    "tipo": "Não entendi se é ganho ou perdido.",
    "etapa_invalida": "Essa etapa não existe no funil.",
    "use_fechar": "Pra encerrar o lead use Ganho ou Perdido.",
    "login": "Sua sessão expirou — entre de novo.",
}


def _erro(r: dict) -> str:
    e = r.get("erro") or ""
    return _RECADO.get(e, e or "Não deu certo.")


def _agir(request: Request, lead_id: int, fn, destino: str):
    """Um caminho, duas respostas. Veio do deslizar (`x-cockpit: 1`)? Devolve JSON e
    o card se resolve sem sair da lista. Veio de um form? Redireciona com o recado
    na sessão, que é como o resto do app funciona."""
    swipe = request.headers.get("x-cockpit") == "1"
    sess = _sessao(request)
    if not sess:
        if swipe:
            return JSONResponse({"ok": False, "erro": _RECADO["login"]}, status_code=401)
        return RedirectResponse("/cockpit/login", status_code=303)
    r = fn(get_pool(), sess[0], sess[1], lead_id)
    if swipe:
        return JSONResponse({"ok": bool(r.get("ok")), "erro": "" if r.get("ok") else _erro(r)})
    request.session["ck_ok" if r.get("ok") else "ck_err"] = (
        r.get("msg", "Feito ✓") if r.get("ok") else _erro(r))
    return RedirectResponse(destino, status_code=303)


@router.post("/cockpit/lead/{lead_id}/audio")
async def cockpit_lead_audio(request: Request, lead_id: int, seg: int = 0):
    """Recebe o áudio que o vendedor gravou. Corpo = os BYTES, não JSON.

    Base64 num JSON custaria +33% de banda no 4G do vendedor e de memória aqui —
    e é a memória que já quase derrubou o serviço de WhatsApp uma vez.

    `seg` e a onda (cabeçalho X-Onda) vêm da tela: é o que faz o áudio do iPhone
    passar sem conversão nenhuma, porque assim o Baileys não precisa decodificar.
    """
    sess = _sessao(request)
    if not sess:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    from finance import audio_voz as av
    dados = await request.body()
    if not dados or len(dados) > av.LIMITE_BYTES:
        return JSONResponse({"ok": False, "erro": "Áudio vazio ou grande demais."})
    onda = None
    cab = request.headers.get("x-onda") or ""
    if cab:
        try:
            import base64
            w = base64.b64decode(cab)
            onda = w if len(w) == 64 else None
        except Exception:  # noqa: BLE001 — onda é enfeite; sem ela o áudio vai igual
            onda = None
    tipo = (request.headers.get("content-type") or "audio/webm").split(",")[0].strip()
    r = ck.enviar_audio(get_pool(), sess[0], sess[1], lead_id, dados, tipo, seg, onda)
    return JSONResponse(r)


@router.post("/cockpit/lead/{lead_id}/mensagem")
def cockpit_mensagem(request: Request, lead_id: int, texto: str = Form(...)):
    return _agir(request, lead_id,
                 lambda p, c, m, l: {**ck.enviar_mensagem(p, c, m, l, texto), "msg": "Mensagem enviada ✓"},
                 f"{_BASE}/lead/{lead_id}")


@router.get("/cockpit/lead/{lead_id}/mensagens")
def cockpit_lead_mensagens(request: Request, lead_id: int, desde: int = 0):
    """O que chegou depois da mensagem `desde`. Alimenta a conversa que se atualiza
    sozinha. Passa por `lead_do_vendedor` de propósito: é ele quem revalida a posse,
    e nenhuma rota daqui pode devolver conversa de outro vendedor."""
    sess = _sessao(request)
    if not sess:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    d = ck.lead_do_vendedor(get_pool(), sess[0], sess[1], lead_id)
    if not d:
        return JSONResponse({"ok": False, "erro": "escopo"}, status_code=404)
    novas = [{"id": m["id"], "who": m["who"], "texto": m["texto"]}
             for m in d["mensagens"] if (m.get("id") or 0) > desde]
    return JSONResponse({"ok": True, "ia": bool(d["ia"]), "msgs": novas})


@router.get("/cockpit/fila/sinal")
def cockpit_fila_sinal(request: Request):
    """Assinatura da fila, pra tela saber se vale recarregar."""
    sess = _sessao(request)
    if not sess:
        return JSONResponse({"ok": False}, status_code=401)
    return JSONResponse({"ok": True, "sig": ck.sinal_fila(get_pool(), sess[0], sess[1])})


@router.post("/cockpit/lead/{lead_id}/ficha")
def cockpit_ficha(request: Request, lead_id: int, empresa: str = Form(""), contato: str = Form(""),
                  cargo: str = Form(""), segmento: str = Form(""), telefone: str = Form(""),
                  whatsapp: str = Form(""), documento: str = Form(""), email: str = Form(""),
                  cep: str = Form(""), endereco: str = Form(""), numero: str = Form(""),
                  bairro: str = Form(""), nascimento: str = Form(""),
                  cidade: str = Form(""), uf: str = Form(""), obs: str = Form("")):
    """Ficha do cliente preenchida pelo vendedor, de dentro da conversa."""
    dados = {"empresa": empresa, "contato": contato, "cargo": cargo, "segmento": segmento,
             "telefone": telefone, "whatsapp": whatsapp, "documento": documento, "email": email,
             "cep": cep, "endereco": endereco, "numero": numero, "bairro": bairro,
             "nascimento": nascimento,
             "cidade": cidade, "uf": uf, "obs": obs}
    return _agir(request, lead_id,
                 lambda p, c, m, l: {**ck.salvar_ficha(p, c, m, l, dados), "msg": "Ficha salva ✓"},
                 f"{_BASE}/lead/{lead_id}")


@router.post("/cockpit/lead/{lead_id}/etapa")
def cockpit_etapa(request: Request, lead_id: int, etapa: str = Form(...)):
    return _agir(request, lead_id,
                 lambda p, c, m, l: {**ck.mudar_etapa(p, c, m, l, etapa), "msg": "Etapa atualizada ✓"},
                 f"{_BASE}/lead/{lead_id}")


@router.post("/cockpit/lead/{lead_id}/assumir")
def cockpit_assumir(request: Request, lead_id: int):
    return _agir(request, lead_id,
                 lambda p, c, m, l: {**ck.assumir(p, c, m, l), "msg": "Você assumiu a conversa ✓"},
                 f"{_BASE}/lead/{lead_id}")


@router.post("/cockpit/lead/{lead_id}/devolver")
def cockpit_devolver(request: Request, lead_id: int):
    return _agir(request, lead_id,
                 lambda p, c, m, l: {**ck.devolver_ia(p, c, m, l), "msg": "Devolvido pro agente ✓"},
                 f"{_BASE}/lead/{lead_id}")


@router.post("/cockpit/lead/{lead_id}/fechar")
def cockpit_fechar(request: Request, lead_id: int, tipo: str = Form(...), motivo: str = Form("")):
    swipe = request.headers.get("x-cockpit") == "1"
    sess = _sessao(request)
    if not sess:
        if swipe:
            return JSONResponse({"ok": False, "erro": _RECADO["login"]}, status_code=401)
        return RedirectResponse("/cockpit/login", status_code=303)
    r = ck.fechar(get_pool(), sess[0], sess[1], lead_id, tipo, motivo)
    if swipe:                                         # o card some da lista sozinho
        return JSONResponse({"ok": bool(r.get("ok")), "erro": "" if r.get("ok") else _erro(r)})
    if r.get("ok"):                                   # fechou: o lead sai da fila
        request.session["ck_ok"] = "Marcado como Ganho 🎉" if tipo == "ganho" else "Marcado como Perdido."
        return RedirectResponse(_BASE, status_code=303)
    request.session["ck_err"] = _erro(r)
    return RedirectResponse(f"{_BASE}/lead/{lead_id}", status_code=303)


@router.post("/cockpit/perfil/push")
def cockpit_push(request: Request, on: str = Form("1")):
    sess = _sessao(request)
    if not sess:
        return RedirectResponse("/cockpit/login", status_code=303)
    ck.set_push(get_pool(), sess[0], sess[1], on == "1")
    return RedirectResponse(f"{_BASE}/perfil", status_code=303)


@router.post("/cockpit/perfil/rodizio")
def cockpit_rodizio(request: Request, on: str = Form("1")):
    # o toggle diz "receber no rodízio"; pausado = NÃO receber → inverte
    sess = _sessao(request)
    if not sess:
        return RedirectResponse("/cockpit/login", status_code=303)
    ck.set_pausado(get_pool(), sess[0], sess[1], on != "1")
    return RedirectResponse(f"{_BASE}/perfil", status_code=303)


@router.post("/cockpit/equipe/reatribuir")
def cockpit_reatribuir(request: Request, lead_id: int = Form(...), para: str = Form("")):
    g = _gerencia(request)
    if not g:
        return RedirectResponse(_BASE, status_code=303)
    destino = f"{_BASE}/lead/{lead_id}"
    if para.strip().isdigit():
        r = cd.reatribuir(get_pool(), g[0], lead_id, int(para))
        request.session["ck_ok" if r.get("ok") else "ck_err"] = (
            "Lead passado pro time ✓" if r.get("ok") else r.get("erro", "Não consegui reatribuir."))
    return RedirectResponse(destino, status_code=303)


@router.post("/cockpit/equipe/pausar")
def cockpit_pausar(request: Request, membro_id: int = Form(...), on: str = Form("1")):
    g = _gerencia(request)
    if not g:
        return RedirectResponse(_BASE, status_code=303)
    cd.pausar(get_pool(), g[0], membro_id, on == "1")
    request.session["ck_ok"] = "Rodízio pausado ✓" if on == "1" else "Vendedor reativado ✓"
    return RedirectResponse(f"{_BASE}/equipe/vendedor/{membro_id}", status_code=303)


# ================================================================== encanamento
# Daqui pra baixo é o que faz o app existir e não aparece em tela: entrar, sair,
# push e o PWA. Veio do app anterior praticamente como estava — é infraestrutura,
# não visual, e reescrever ia trocar risco por nada.

# ------------------------------------------------------------------ login mágico
def _tela_login(titulo: str, sub: str, email: str = "", erro: str = "",
                nota: str = "") -> HTMLResponse:
    """A tela de entrada. Senha é o caminho PRINCIPAL; o link por e-mail fica como
    saída pra quem esqueceu ou nunca criou senha.

    Era o contrário: só link mágico, com a frase "Sem senha". Como é aqui que o
    vendedor aterrissa quando a sessão cai, ele ficava dependendo de e-mail chegar e
    de abrir em 15 min — no meio do expediente. E-mail e senha já existiam
    (contas/equipe.py), só não eram oferecidos neste lugar."""
    aviso = (f"<div class=erro>{esc(erro)}</div>" if erro else
             (f"<div class=nota>{esc(nota)}</div>" if nota else ""))
    corpo = ("<div class=login><div class=marca>Zaq</div>"
             f"<h2>{esc(titulo)}</h2><p>{esc(sub)}</p>"
             f"{aviso}"
             # UM FORMULÁRIO SÓ, dois botões. Eram dois <form>: o de baixo levava um
             # e-mail ESCONDIDO, preenchido pelo servidor — ou seja, nunca via o que
             # a pessoa tinha acabado de digitar no de cima. Pedir link mandava pra
             # endereço vazio, e o campo vazio ainda estourava um 422 em JSON cru na
             # cara do vendedor. Com um form só, os dois botões leem o mesmo campo.
             f"<form method=post action='{_BASE}/login'>"
             f"<input name=email type=email required placeholder='seu e-mail' "
             f"autocomplete=username value='{esc(email)}'>"
             "<input name=senha type=password placeholder='sua senha' "
             "autocomplete=current-password>"
             # marcado por padrão: é o aparelho de trabalho do vendedor, e o pedido
             # que originou isto foi justamente não ter que entrar de novo.
             "<label class=chk><input type=checkbox name=lembrar value=1 checked>"
             "Manter conectado neste aparelho</label>"
             "<button class=go type=submit>Entrar</button>"
             # `name` no BOTÃO: só é enviado quando ELE é o clicado. É assim que o
             # servidor sabe qual das duas portas a pessoa escolheu, sem JS.
             "<button class=go2 type=submit name=so_link value=1>"
             "Entrar por link no e-mail</button></form>"
             "<small>Esqueceu a senha? Use o link por e-mail e crie outra ao entrar.</small>"
             "</div>")
    return _page("Zaq — entrar", corpo)


@router.get("/cockpit/login", response_class=HTMLResponse)
def cockpit_login_form(request: Request, enviado: str = "", expirou: str = ""):
    if _sessao(request):
        return RedirectResponse(_BASE, status_code=303)
    if enviado:
        # a mesma resposta pra e-mail que existe e pra e-mail que não existe:
        # dizer "não achei" contaria quem é do time pra quem só chutou o endereço
        corpo = ("<div class=login><div class=marca>Zaq</div><h2>Confira seu e-mail</h2>"
                 "<p>Se esse e-mail estiver cadastrado, você recebeu um link pra entrar. "
                 "Ele vale por 15 minutos.</p>"
                 f"<a class=go2 href='{_BASE}/login'>Voltar</a>"
                 "<small>Não chegou? Veja o spam ou peça o link ao seu gestor.</small></div>")
        return _page("Zaq — confira o e-mail", corpo)
    if expirou:
        return _tela_login("Entre de novo", "Sua sessão expirou neste aparelho.",
                           nota="Seus leads continuam aí — nada se perdeu.")
    return _tela_login("Seus leads, no bolso", "Entre com seu e-mail e senha.")


@router.post("/cockpit/login")
def cockpit_login(request: Request, email: str = Form(""), senha: str = Form(""),
                  lembrar: str = Form(""), so_link: str = Form("")):
    """Duas portas no mesmo endereço: com senha entra na hora; sem senha (ou pelo
    botão "entrar por link") cai no e-mail de sempre.

    NENHUM campo é obrigatório aqui, de propósito. Com `Form(...)` o FastAPI devolve
    422 em JSON cru — e foi o que o vendedor viu na tela do celular: um arquivo
    `login.json` pra baixar. Validação de formulário é resposta de tela, não de API."""
    # valida ANTES de pegar o pool: formulário vazio não precisa de banco.
    email = (email or "").strip()
    if not email:
        return _tela_login("Seus leads, no bolso", "Entre com seu e-mail e senha.",
                           erro="Digite seu e-mail.")
    pool = get_pool()
    if senha and not so_link:
        from contas import equipe as _equipe
        # `contextos_de_login`, NÃO `autenticar`. A identidade aqui é por E-MAIL, e a
        # mesma pessoa pode ser dona de uma conta e vendedora de outras — uma senha,
        # vários lugares. `autenticar` só olha `membros.senha_hash`; quem tem conta
        # própria e entrou como membro numa empresa ficava de fora, porque a senha
        # dele mora em `contas`, e o membro nasce SEM senha nenhuma.
        #
        # Foi o que aconteceu em 18/08: o mesmo e-mail existia como conta (com senha)
        # e como membro "vendedor" da Prime (sem senha). A pessoa trocou a senha duas
        # vezes — da CONTA — e o Cockpit continuou recusando, porque lia a do MEMBRO.
        # Pelo painel entrava, porque o portal já usa esta função.
        ctxs = [x for x in _equipe.contextos_de_login(pool, email, senha)
                if (x.get("papel") or "") in _PAPEIS_OK]
        if not ctxs:
            # mensagem única pra senha errada e pra e-mail que não é do time: dizer
            # qual dos dois falhou entrega quem tem cadastro.
            return _tela_login("Seus leads, no bolso", "Entre com seu e-mail e senha.",
                               email=email, erro="E-mail ou senha incorretos.")
        if len(ctxs) > 1:
            # Trabalha em mais de um lugar: quem escolhe é ela. Entrar na empresa
            # errada é pior que um toque a mais — o vendedor mexeria no funil de
            # outra empresa achando que é o dele.
            request.session["ck_ctxs"] = ctxs
            request.session["ck_lembrar"] = bool(lembrar)
            return _tela_empresas(ctxs)
        return _entrar_no_contexto(request, ctxs[0], bool(lembrar))
    achado = ck.membro_por_email(pool, email)
    if achado:                       # nunca revela se existe: sempre redireciona igual
        try:
            token = ck.gerar_token(pool, achado["conta_id"], achado["membro_id"])
            _enviar_link_email(pool, achado["conta_id"], email, ck.link_acesso(token))
        except Exception:  # noqa: BLE001
            pass
    return RedirectResponse(f"{_BASE}/login?enviado=1", status_code=303)


def _pôr_lembrete(request: Request, resp, conta_id: int, membro_id: int) -> None:
    """Grava o aparelho e põe o cookie SEM prazo de expiração.

    `max_age` de 10 anos é o "indeterminado" na prática — cookie sem max_age morre ao
    fechar o navegador, que é o oposto do pedido. Quem encerra de verdade é a
    revogação no banco (Sair, membro desativado), não o relógio.

    httponly: JS da página não lê — se algum script de terceiro entrar, não leva a
    sessão junto. secure: só por HTTPS, igual ao cookie do portal."""
    token = ck.lembrar_criar(get_pool(), conta_id, membro_id,
                             (request.headers.get("user-agent") or "")[:120])
    if not token:
        return
    resp.set_cookie(
        ck.LEMBRETE_COOKIE, token, max_age=60 * 60 * 24 * 3650, path="/cockpit",
        httponly=True, samesite="lax",
        secure=_os.environ.get("PORTAL_COOKIE_SECURE", "1") == "1")


def _entrar_no_contexto(request: Request, ctx: dict, lembrar: bool):
    """Aplica o contexto escolhido na sessão e entra. `aplicar_contexto` é a mesma
    função do portal — a sessão do Cockpit e a do painel são a mesma sessão, e
    escrever os campos na mão aqui abriria espaço pra elas divergirem."""
    from contas import equipe as _equipe
    _equipe.aplicar_contexto(request.session, ctx)
    request.session["cockpit"] = True
    resp = RedirectResponse(_BASE, status_code=303)
    if lembrar and ctx.get("membro_id"):
        # sem membro_id não há quem lembrar: o dono da conta não é linha de `membros`,
        # e `cockpit_lembrete` referencia membro. Ele segue pela sessão normal.
        _pôr_lembrete(request, resp, ctx["conta_id"], ctx["membro_id"])
    return resp


def _tela_empresas(ctxs: list[dict]) -> HTMLResponse:
    """Escolha da empresa, pra quem trabalha em mais de uma."""
    botoes = "".join(
        f"<form method=post action='{_BASE}/empresa' style='margin:0'>"
        f"<input type=hidden name=i value='{n}'>"
        f"<button class=go2 type=submit>{esc(x.get('nome') or 'Empresa')}"
        f"<br><small style='opacity:.7'>{esc(_equipe_rotulo(x))}</small></button></form>"
        for n, x in enumerate(ctxs))
    corpo = ("<div class=login><div class=marca>Zaq</div>"
             "<h2>Onde você vai trabalhar?</h2>"
             "<p>Seu acesso vale em mais de um lugar. Escolha um — dá pra trocar "
             "depois saindo e entrando de novo.</p>"
             f"{botoes}</div>")
    return _page("Zaq — escolher empresa", corpo)


def _equipe_rotulo(ctx: dict) -> str:
    from contas import equipe as _equipe
    return "Sua conta" if not ctx.get("membro_id") else _equipe.rotulo(ctx.get("papel"))


@router.post("/cockpit/empresa")
def cockpit_empresa(request: Request, i: str = Form("")):
    """Confirma a empresa escolhida na tela acima.

    Relê os contextos da SESSÃO, gravados no login que já validou a senha — o índice
    que vem do formulário só escolhe entre eles. Assim ninguém entra numa empresa
    mandando um número diferente: o que limita é a lista que a própria senha abriu."""
    ctxs = request.session.get("ck_ctxs") or []
    try:
        ctx = ctxs[int(i)]
    except (ValueError, IndexError, TypeError):
        return RedirectResponse(f"{_BASE}/login", status_code=303)
    lembrar = bool(request.session.pop("ck_lembrar", False))
    request.session.pop("ck_ctxs", None)
    return _entrar_no_contexto(request, ctx, lembrar)


@router.get("/cockpit/entrar/{token}", response_class=HTMLResponse)
def cockpit_entrar(request: Request, token: str):
    dados = ck.validar_token(get_pool(), token)
    if not dados:
        corpo = ("<div class=login><div class=marca>Zaq</div><h2>Link expirado</h2>"
                 "<p>Esse link já foi usado ou passou dos 15 minutos.</p>"
                 f"<a class=go href='{_BASE}/login'>Pedir um novo link</a></div>")
        return _page("Zaq — link expirado", corpo)
    request.session["conta_id"] = dados["conta_id"]
    request.session["membro_id"] = dados["membro_id"]
    request.session["papel"] = dados["papel"]
    request.session["cockpit"] = True
    # quem entrou por link JÁ provou o e-mail: o aparelho fica lembrado igual a quem
    # entrou por senha. Sem isto, o vendedor que só usa link seguiria caindo fora.
    destino = _BASE if ck.tem_senha(get_pool(), dados["conta_id"], dados["membro_id"]) \
        else f"{_BASE}/senha"
    resp = RedirectResponse(destino, status_code=303)
    _pôr_lembrete(request, resp, dados["conta_id"], dados["membro_id"])
    return resp


@router.get("/cockpit/sair")
def cockpit_sair(request: Request):
    """Limpa a sessão INTEIRA, igual ao /sair do painel.

    Antes esta função tirava só `cockpit`, `membro_id` e `papel` e deixava o
    `conta_id` pra trás. Só que `_gerencia` lê o papel com `get("papel", "dono")`
    — sem papel na sessão, o padrão é dono. Resultado: quem apertava Sair caía de
    volta em /cockpit e via a visão de equipe inteira, com a carteira de propostas
    do time e o botão de fechar contrato. Sair tem que sair.
    """
    # revoga ESTE aparelho antes de limpar a sessão: sem isto o cookie de "manter
    # conectado" reconstruiria a sessão no request seguinte e o Sair não sairia.
    # Os outros aparelhos do vendedor continuam conectados, que é o esperado.
    token = request.cookies.get(ck.LEMBRETE_COOKIE)
    if token:
        ck.lembrar_revogar(get_pool(), token)
    request.session.clear()
    resp = RedirectResponse(f"{_BASE}/login", status_code=303)
    resp.delete_cookie(ck.LEMBRETE_COOKIE, path="/cockpit")
    return resp


@router.get("/cockpit/senha", response_class=HTMLResponse)
def cockpit_senha_form(request: Request, erro: str = "", trocar: str = ""):
    """Criar (ou trocar) a senha, sem sair do app.

    Aparece uma vez, logo depois do link mágico, pra quem ainda não tem senha — e é o
    que tira o vendedor da dependência de e-mail. Grava na MESMA coluna do login web
    (migração 072): uma credencial só, Cockpit e painel."""
    sess = _sessao(request)
    if not sess:
        return RedirectResponse(f"{_BASE}/login", status_code=303)
    novo_login = not trocar
    corpo = ("<div class=login><div class=marca>Zaq</div>"
             f"<h2>{'Crie sua senha' if novo_login else 'Trocar senha'}</h2>"
             "<p>Assim você entra direto da próxima vez, sem depender do e-mail.</p>"
             + (f"<div class=erro>{esc(erro)}</div>" if erro else "")
             + f"<form method=post action='{_BASE}/senha'>"
             "<input name=senha type=password required minlength=8 maxlength=72 "
             "placeholder='nova senha' autocomplete=new-password>"
             "<input name=confirma type=password required minlength=8 maxlength=72 "
             "placeholder='repetir a senha' autocomplete=new-password>"
             "<button class=go type=submit>Salvar e continuar</button></form>"
             "<small>Mínimo de 8 caracteres. Dá pra trocar depois no Perfil.</small>"
             + (f"<a class=go2 href='{_BASE}'>Agora não</a>" if novo_login else "")
             + "</div>")
    return _page("Zaq — senha", corpo)


@router.post("/cockpit/senha")
def cockpit_senha_salva(request: Request, senha: str = Form(...),
                        confirma: str = Form("")):
    sess = _sessao(request)
    if not sess:
        return RedirectResponse(f"{_BASE}/login", status_code=303)
    if senha != confirma:
        return cockpit_senha_form(request, erro="As senhas não conferem.")
    r = ck.definir_senha(get_pool(), sess[0], sess[1], senha)
    if not r.get("ok"):
        return cockpit_senha_form(request, erro=r.get("erro") or "Não deu pra salvar.")
    return RedirectResponse(_BASE, status_code=303)


# um envio de acesso que falha em SILÊNCIO é o pior caso: o membro fica de fora e
# ninguém fica sabendo. Este log é o rastro que faltava em 18/08.
_log_link = _logging.getLogger("cockpit.acesso")


def _enviar_link_email(pool, conta_id: int, email: str, link: str) -> bool:
    """Manda o link mágico PELO REMETENTE DO ZAQ, com a caixa da empresa como reserva.

    A ordem já foi a inversa — empresa primeiro — e foi assim que o link sumiu. Em
    18/08 um membro pediu acesso na Prime Eventos: o token nasceu e ficou válido, e o
    e-mail nunca chegou. Pelo painel, o mesmo pedido chegou na hora — porque o portal
    manda por este mesmo remetente, sem passar pela caixa do cliente.

    E o plano B não salvava, porque só entra quando o envio RECUSA. Quando o Gmail da
    empresa ACEITA e a mensagem morre depois (filtro do destino, spam, caixa com
    problema), a função devolve True, a reserva nunca roda, e a tela diz "confira seu
    e-mail" com toda a tranquilidade. Falha silenciosa com cara de sucesso.

    A REGRA, agora explícita: mensagem pra LEAD sai pela caixa da empresa — a resposta
    dele tem que cair no inbox dela. Link de acesso é E-MAIL DE SISTEMA: ninguém
    responde, só precisa chegar, e a entrega manda mais que o remetente. Sai pelo Zaq.

    A caixa da empresa continua como reserva pra quando o SMTP do Zaq não estiver
    configurado — aí é melhor sair por ela do que não sair."""
    titulo = "Seu acesso ao Zaq"
    corpo = ("Toque no botão pra entrar e atender seus leads. "
             "O link vale por 15 minutos e abre no seu aparelho.")
    try:
        from finance import email_sender as es
        # hex literal, não token: e-mail não tem CSS custom property que resolva
        botao = (f'<div style="text-align:center;margin:24px 0">'
                 f'<a href="{esc(link)}" style="background:#25D366;color:#04150C;padding:14px 28px;'
                 f'border-radius:10px;font-weight:600;text-decoration:none;display:inline-block">'
                 f'Entrar no Zaq →</a></div>'
                 f'<p style="color:#888;font-size:13px">Ou copie: {esc(link)}</p>')
        html = es._layout(titulo, f"<p>{esc(corpo)}</p>{botao}")
        texto = f"{corpo}\n\n{link}"
        with pool.connection() as c:
            nome_emp = (c.execute("select nome from contas where id=%s",
                                  (conta_id,)).fetchone() or [""])[0]
        # 1) remetente do Zaq — o mesmo caminho da recuperação de senha do portal,
        #    que é o que comprovadamente entrega. `from_nome` mantém o nome da
        #    empresa visível pra quem recebe: muda quem carrega, não quem assina.
        # `enviar_email` já devolve False quando falta config — não precisa de um
        # gate a mais, que só criaria um segundo jeito de decidir a mesma coisa.
        if es.enviar_email(email, titulo, html, texto, from_nome=nome_emp or None):
            return True
        _log_link.warning("conta %s: o Zaq não mandou o link de acesso — "
                          "tentando pela caixa da empresa", conta_id)
        # 2) reserva: a caixa da empresa. Só quando o Zaq não pôde mandar.
        try:
            from finance import email_inbound as ein
            return bool(ein.enviar_conta(pool, conta_id, email, titulo, html, texto,
                                         from_nome=nome_emp or None))
        except Exception as e:  # noqa: BLE001
            _log_link.warning("conta %s: nem o Zaq nem a caixa da empresa mandaram o "
                              "link de acesso: %s: %s", conta_id, type(e).__name__, e)
            return False
    except Exception:  # noqa: BLE001
        return False


# ------------------------------------------------------------------ push
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


# ------------------------------------------------------------------ convite da visita
@router.get("/visita/{token}.ics", include_in_schema=False)
def visita_ics_publico(request: Request, token: str):
    ics = ck.visita_ics(get_pool(), token)
    if not ics:
        return Response("visita não encontrada", status_code=404)
    return Response(ics, media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="visita.ics"'})


# ------------------------------------------------------------------ instalar como app
# Cores em hex, de propósito. Estes dois arquivos saem SOZINHOS — o SVG é servido
# como image/svg+xml e o manifest é JSON —, então não existe `:root` nenhum pra
# `var(--bg)` resolver. Os valores abaixo são os mesmos tokens da marca, escritos
# à mão: --bg #0A0F0C, --neon-fundo #10241A, --neon #25D366.
_ICON_SVG = ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 512 512'>"
             "<rect width='512' height='512' rx='96' fill='#0A0F0C'/>"
             "<rect x='40' y='40' width='432' height='432' rx='80' fill='#10241A'/>"
             "<path d='M170 150 h150 L190 362 h150' stroke='#25D366' stroke-width='34' "
             "fill='none' stroke-linecap='round' stroke-linejoin='round'/></svg>")


@router.get("/cockpit/icon.svg", include_in_schema=False)
def cockpit_icon():
    return Response(_ICON_SVG, media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=604800"})


@router.get("/cockpit/manifest.webmanifest", include_in_schema=False)
def cockpit_manifest():
    import json
    m = {
        "name": "Zaq — meus leads", "short_name": "Zaq",
        "start_url": _BASE, "scope": _BASE, "display": "standalone",
        "background_color": "#0A0F0C", "theme_color": "#0A0F0C",
        "description": "Receba e atenda seus leads.",
        "icons": [
            {"src": "/cockpit/icon.svg", "sizes": "any", "type": "image/svg+xml",
             "purpose": "any maskable"},
        ],
    }
    return Response(json.dumps(m), media_type="application/manifest+json",
                    headers={"Cache-Control": "public, max-age=86400"})


# A chave do cache virou v2 na troca de app. A estratégia é rede-primeiro-com-
# reserva: quem instalou o app antigo tem o shell antigo guardado, e sem trocar a
# chave ele voltaria do cache na primeira vez que a rede falhasse — o visual velho
# reaparecendo sozinho. Chave nova, cache velho descartado no activate.
_SW = """
const CACHE='cockpit-v3';
self.addEventListener('install',e=>{self.skipWaiting();});
self.addEventListener('activate',e=>{e.waitUntil(
  caches.keys().then(ks=>Promise.all(ks.filter(k=>k!==CACHE).map(k=>caches.delete(k))))
    .then(()=>self.clients.claim()));});

// O que é ESTÁTICO e igual em toda tela: a folha, as fontes, o ícone, o splash e
// o manifest. Antes tudo era rede-primeiro, então o app pagava a viagem completa
// até pra desenhar o que já tinha em disco.
// A folha e as fontes têm URL imutável (hash no ?v= / nome fixo), então guardar
// pra sempre é seguro: conteúdo novo chega com endereço novo.
const ESTATICO=/^\\/(cockpit\\/(app\\.css|icon\\.svg|splash\\/|manifest\\.webmanifest)|estatico\\/fontes\\/)/;

self.addEventListener('fetch',e=>{
  const r=e.request; if(r.method!=='GET'){return;}

  // HTML segue REDE-PRIMEIRO, de propósito: a fila, a conversa e os contadores
  // mudam a cada minuto, e servir uma tela velha do disco seria pior que esperar.
  // O cache continua sendo a reserva pra quando a rede falha.
  if(!ESTATICO.test(new URL(r.url).pathname)){
    e.respondWith(fetch(r).then(res=>{
      try{const cp=res.clone();caches.open(CACHE).then(c=>c.put(r,cp));}catch(_){}
      return res;
    }).catch(()=>caches.match(r)));
    return;
  }

  // Estático: responde do disco NA HORA e revalida por trás (stale-while-
  // revalidate). A tela nunca espera por isso, e a versão nova entra na próxima.
  e.respondWith(caches.match(r).then(cacheado=>{
    const rede=fetch(r).then(res=>{
      try{const cp=res.clone();caches.open(CACHE).then(c=>c.put(r,cp));}catch(_){}
      return res;
    }).catch(()=>cacheado);
    return cacheado||rede;
  }));
});
self.addEventListener('push',e=>{
  let d={title:'Novo lead',body:'Toque pra atender'};
  try{d=Object.assign(d,e.data.json());}catch(_){}
  const tarefas=[self.registration.showNotification(d.title,{body:d.body,icon:'/cockpit/icon.svg',
    badge:'/cockpit/icon.svg',data:{url:d.url||'/cockpit'}})];
  // A bolinha no ÍCONE do app. O service worker acorda com o push mesmo com o app
  // fechado, então é aqui — e só aqui — que dá pra marcar o ícone sem o vendedor
  // abrir nada. A notificação passa; a bolinha fica até ele responder.
  // `setAppBadge` não existe em todo navegador (e no iOS só em app instalado na
  // tela de início): onde não existir, isto não faz nada e o push segue igual.
  if(typeof d.badge_n==='number'&&self.navigator&&self.navigator.setAppBadge){
    tarefas.push(d.badge_n>0?self.navigator.setAppBadge(d.badge_n)
                            :self.navigator.clearAppBadge());
  }
  e.waitUntil(Promise.all(tarefas.map(p=>Promise.resolve(p).catch(()=>{}))));
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
        request.session["equipe_erro"] = "Só dá pra gerar o link do app pra vendedor/gestor ativo."
        return RedirectResponse("/painel/equipe", status_code=303)
    token = ck.gerar_token(pool, conta[0], membro_id)
    link = ck.link_acesso(token)
    request.session["equipe_link"] = link
    request.session["equipe_link_cap"] = "🔗 Link do app gerado — mande pra pessoa (vale 15 min):"
    enviado = False
    if m[1] and "@" in (m[1] or ""):
        enviado = _enviar_link_email(pool, conta[0], m[1], link)
    request.session["equipe_aviso"] = (
        f"Link do app gerado e enviado por e-mail para {m[1]} ✓ (vale 15 min)."
        if enviado else
        "Link do app gerado abaixo — mande pra pessoa (vale 15 min).")
    return RedirectResponse("/painel/equipe", status_code=303)
