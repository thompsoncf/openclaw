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
from starlette.concurrency import run_in_threadpool
from fastapi.responses import (HTMLResponse, JSONResponse, RedirectResponse, Response,
                               StreamingResponse)

from db.conexao import get_pool
from web import tema as _tema
from finance import cockpit as ck
from finance import cockpit_dono as cd

router = APIRouter()

_BASE = "/cockpit"
_log = _logging.getLogger("cockpit.novidades")
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
/* A FAIXA DE BAIXO QUE NÃO DÁ PRA USAR.
   `viewport-fit=cover` (ver _page) manda desenhar por baixo das barras do sistema —
   é o que tira a tarja preta do topo no app instalado. Quem faz isso tem que devolver
   o espaço embaixo também, e até aqui a conta era só `env(safe-area-inset-bottom)`.
   Medido num Chromium com o CSS real: quando o `env()` não reporta nada, o rodapé
   reservava 12,8px e o botão ficava a 13px do fim da tela. A barra de navegação do
   Android (os três botões) come ~48px — o "Marcar" nascia atrás dela, e o vendedor
   via só a borda verde de cima. É o print que o dono mandou em 23/08.
   O piso resolve porque `max()` deixa o `env()` vencer onde ele funciona (iPhone
   reporta ~34px) e só entra quando ele volta zero. Generoso de propósito: sobra vira
   espaço em branco, falta vira botão que ninguém alcança. */
:root{--fundo-seguro:max(env(safe-area-inset-bottom,0px),3rem)}
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
/* "aberto, sem resposta": entre o âmbar de "sua vez" e o verde de "respondido".
   Apagado de propósito — ele informa, não cobra; quem cobra é a bolinha. */
.chip.aberto{color:var(--txt-mut);border-color:var(--line);background:transparent}
.chip.err{color:var(--coral);border-color:#5a2b2b;background:#241313}
/* de fora do mês (mockup cockpit_mes_atual): o mês em que entrou */
.chip.entrou{color:#F0DCA6;border-color:#5a4520;background:#241c0f}
.lead.fora{background:rgba(224,163,46,.05);box-shadow:inset 3px 0 0 #5a4520}
/* a barra de foco: período e as pílulas do que ficou de fora, rolando pro lado */
.foco{display:flex;gap:.35rem;padding:.35rem 1.1rem .55rem;overflow-x:auto;align-items:center;scrollbar-width:none}
.foco::-webkit-scrollbar{display:none}
.foco .pil{flex:none;display:inline-flex;align-items:center;gap:.3rem;border:1px solid var(--line);border-radius:999px;
  padding:.3rem .65rem;font-size:.76rem;color:var(--text-dim);background:var(--surface);text-decoration:none}
.foco .pil b{font-family:var(--mono);font-weight:500;font-size:.7rem;color:var(--text)}
.foco .pil.on{background:var(--neon);border-color:var(--neon);color:var(--ink);font-weight:600}
.foco .pil.on b{color:var(--ink)}
.foco .pil.fora{border-color:#5a4520;background:#241c0f;color:#F0DCA6}
.foco .pil.fora b{color:#F0DCA6}
.foco .pil.fora.on{background:var(--ambar);border-color:var(--ambar);color:#1c1408}
.foco .pil.fora.on b{color:#1c1408}
.foco .sep{flex:none;width:1px;height:18px;background:var(--line);margin:0 .2rem}
/* os grupos da fila e a dobra dos parados */
.grp{display:flex;align-items:center;gap:.4rem;padding:.6rem 1.1rem .25rem;font-size:.66rem;text-transform:uppercase;
  letter-spacing:.07em;color:var(--text-faint);font-weight:600}
.grp b{font-family:var(--mono);font-weight:500;color:var(--text-dim);letter-spacing:0}
.grp .ln{flex:1;height:1px;background:var(--line)}
.dobra>summary{list-style:none;cursor:pointer}
.dobra>summary::-webkit-details-marker{display:none}
.dobra>summary .grp{color:#F0DCA6}
.dobra>summary .grp::after{content:'▸';color:var(--text-faint)}
.dobra[open]>summary .grp::after{content:'▾'}
/* a linha do evento no card */
.lead .ev{display:flex;align-items:center;gap:.3rem;flex-wrap:wrap;font-size:.72rem;color:var(--text-dim);margin-top:.1rem}
.lead .ev b{color:var(--text)} .lead .ev .d{font-style:normal;font-family:var(--mono);color:var(--neon)}
.lead .ev .src{font-style:normal;font-size:.56rem;color:#7bb8e6;border:1px solid #1f3a4d;background:#122029;border-radius:999px;padding:0 .32rem}
.lead .ev.sem{color:#F0DCA6}
.lead .ev .perg{margin-left:auto;font-size:.68rem;font-weight:600;color:#F0DCA6;border:1px solid #5a4520;background:#241c0f;border-radius:999px;padding:.06rem .5rem}
.aviso.pista{text-align:left;margin:.6rem 1.1rem 0;border:1px solid #5a4520;background:#241c0f;color:#F0DCA6;border-radius:12px;padding:.55rem .7rem;font-size:.8rem;display:flex;flex-direction:column;gap:.35rem}
.aviso.pista .bts{display:flex;gap:.4rem}
.aviso.pista .bt{font-size:.72rem;font-weight:600;padding:.3rem .6rem;border-radius:8px;border:1px solid #5a4520;color:#F0DCA6;text-decoration:none}
.aviso.pista .bt.ok{background:var(--ambar);color:#1c1408;border-color:var(--ambar)}

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
.acoes a.acao-forte{border-color:var(--neon-borda);background:var(--neon-fundo);
  color:var(--neon);font-weight:600}
.acoes a.acao-apagar{border-color:var(--coral-borda);background:var(--coral-fundo);
  color:#f0b8b8;font-weight:600}
/* "hoje está marcada pra" — o de-para tem que estar à vista na hora de escolher a
   data nova, senão a pessoa remarca sem lembrar de quando era. */
.agora-e{background:var(--bg-2);border:1px solid var(--line);border-radius:11px;
  padding:.7rem .85rem;margin-bottom:1rem}
.agora-e .r{font-size:.66rem;color:var(--text-faint);letter-spacing:.1em;
  text-transform:uppercase;font-weight:700;margin-bottom:.25rem}
.agora-e b{font-size:.92rem;display:block}
.agora-e .q{font-family:var(--mono);font-size:.8rem;color:var(--text-dim);margin-top:.2rem}
.fic-ck{display:flex;align-items:flex-start;gap:.6rem;padding:.7rem .8rem;
  border:1px solid var(--line);border-radius:11px;background:var(--surface);
  margin-bottom:.9rem;font-size:.85rem}
.fic-ck input{width:20px;height:20px;accent-color:var(--neon);flex:0 0 auto;margin-top:.05rem}
.fic-ck small{display:block;color:var(--text-dim);font-size:.73rem;margin-top:.1rem}

/* ---------- botões ---------- */
.btn{display:flex;align-items:center;justify-content:center;gap:.5rem;width:100%;
  background:var(--neon);color:var(--ink);font-family:var(--display);font-weight:700;
  font-size:.95rem;padding:.8rem;border:0;border-radius:13px;cursor:pointer;
  box-shadow:0 8px 30px rgba(37,211,102,.32)}
.btn:active{transform:translateY(1px)}
.btn.ghost{background:transparent;color:var(--text-dim);border:1px solid var(--line);box-shadow:none;font-weight:600}
.btn.perigo{background:transparent;color:var(--coral);border:1px solid #5a2b2b;box-shadow:none}
/* tela de Pagamentos: o estado de cada parcela, e o anexo.
   `.arq` é um <label> vestido de botão porque o <input type=file> não se estiliza
   — o input fica escondido dentro dele e o toque no label é que o abre. */
.pill{align-self:flex-start;font-family:var(--mono);font-size:.68rem;letter-spacing:.04em;
  text-transform:uppercase;padding:.16rem .5rem;border-radius:999px;border:1px solid}
.pill.ok{color:var(--neon);border-color:var(--neon-borda);background:var(--neon-fraco)}
.pill.falta{color:var(--coral);border-color:#5a2b2b;background:rgba(224,122,95,.1)}
.pill.esp{color:var(--text-faint);border-color:var(--line);background:transparent}
.btn.arq{font-size:.86rem;padding:.6rem;box-shadow:none}
.btn.arq.on{opacity:.6}
.vlr{font-family:var(--mono);font-variant-numeric:tabular-nums}
.linhaform{display:flex;gap:.45rem;align-items:center}
select{flex:1;min-width:0;background:var(--bg-2);border:1px solid var(--line);border-radius:10px;
  color:var(--text);padding:.55rem .6rem;font-family:inherit;font-size:.85rem}

/* ---------- visita esperando resposta ----------
   Coral e no topo de propósito: é a única coisa na agenda que o vendedor precisa
   RESOLVER, não consultar. Some no instante em que ele responde. */
.pend-tit{display:flex;align-items:center;gap:.4rem;font-size:.7rem;text-transform:uppercase;
  letter-spacing:.06em;color:var(--coral);font-weight:700;padding:.2rem .2rem .55rem}
.pend-tit .cnt{font-family:var(--mono);background:var(--coral-fundo);color:#F0B8B8;
  border:1px solid var(--coral-borda);border-radius:99px;padding:0 .38rem;font-size:.7rem}
.pend{border:1px solid var(--coral-borda);background:var(--coral-fundo);border-radius:12px;
  padding:.7rem .8rem;display:flex;flex-direction:column;gap:.55rem;margin-bottom:.5rem}
.pend-top{display:flex;align-items:baseline;justify-content:space-between;gap:.5rem}
.pend-top b{font-size:.9rem;color:#F6D3D0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pend-top span{font-family:var(--mono);font-size:.7rem;color:#D9AEAA;flex-shrink:0}
.pend-q{font-size:.82rem;color:#E8C4C1}
.pend-bts{display:grid;grid-template-columns:1fr 1fr;gap:.45rem}
.pb{padding:.55rem;border-radius:9px;font-size:.82rem;font-weight:600;border:1px solid;
  font-family:inherit;cursor:pointer}
.pb.sim{background:var(--neon-fundo);color:var(--neon-bright);border-color:var(--neon-borda)}
.pb.nao{background:#241313;color:#F0B8B8;border-color:var(--coral-borda)}
.pb:disabled{opacity:.5}
/* O par de APOIO (Remarcar / Abrir o lead). Sem preenchimento de propósito: o cartão
   existe pra arrancar a RESPOSTA, e o comparecimento é o número que o relatório do
   funil usa. Dar a estes dois o mesmo peso dos de cima seria oferecer uma saída tão
   convidativa quanto responder. */
.pb2{padding:.5rem;border-radius:9px;font-size:.78rem;font-weight:500;
  border:1px solid var(--line);background:transparent;color:var(--text-dim);
  text-align:center;text-decoration:none;display:block;font-family:inherit}
.pend.feito{border-color:var(--line);background:var(--surface)}
.pend.feito .pend-top b,.pend.feito .pend-q{color:var(--text-dim)}
.pend .rea{grid-column:1/-1;background:var(--ambar-fundo);color:#F0DCA6;
  border-color:var(--ambar-borda);text-align:center;display:block;text-decoration:none;
  padding:.55rem;border-radius:9px;font-size:.82rem;font-weight:600;border:1px solid}

/* ---------- chat (tela do lead) ---------- */
.chat{flex:1;overflow-y:auto;padding:.9rem 1.1rem;display:flex;flex-direction:column;gap:.5rem;
  background:#0B141A;position:relative;z-index:1}
.bub{max-width:82%;padding:.5rem .75rem;border-radius:13px;font-size:.87rem;white-space:pre-wrap;
  word-break:break-word}
.bub.in{align-self:flex-start;background:#1F2C25;border-bottom-left-radius:4px}
.bub.out{align-self:flex-end;background:#0A5C49;border-bottom-right-radius:4px}
.bub.ia{align-self:flex-end;background:#1a1226;border:1px solid #3a2b52;border-bottom-right-radius:4px}
.bub .who{font-size:.64rem;color:var(--text-faint);margin-bottom:.15rem}
/* A HORA, como no WhatsApp: pequena, no rodapé da bolha, alinhada à direita.
   `float:right` e não flex porque a bolha é `white-space:pre-wrap` com texto
   corrido — flutuando, a hora se encaixa na última linha quando cabe e desce
   sozinha quando não cabe, que é exatamente o comportamento do WhatsApp.
   O `margin-left` impede que ela encoste na última palavra. */
.bub .hora{float:right;font-size:.6rem;line-height:1.9;opacity:.55;
  margin:0 0 -.15rem .5rem;font-family:var(--mono);white-space:nowrap}
/* A TARJA DO DIA. Fica fora das bolhas, centralizada, e é o que impede a conversa
   de virar uma fila sem tempo: sem ela "20:28" pode ser hoje ou de três semanas
   atrás, e o vendedor abriria o WhatsApp do celular só pra saber. */
.diadia{align-self:center;font-size:.62rem;letter-spacing:.06em;color:var(--text-faint);
  background:var(--bg-2);border:1px solid var(--line);border-radius:999px;
  padding:.12rem .6rem;margin:.35rem 0}
/* A mídia dentro da bolha. O arquivo NÃO está no nosso disco: o src aponta pra
   /cockpit/lead/<lead>/midia/<msg>, que busca no CDN do WhatsApp e decifra na hora. */
.bub .mid{display:block;margin:.15rem 0 .3rem;border-radius:9px;overflow:hidden;
  border:1px solid rgba(255,255,255,.09);background:rgba(0,0,0,.2);max-width:220px}
.bub .mid img,.bub .mid video{display:block;width:100%;height:auto;max-height:260px;
  object-fit:cover}
.bub .mid.fig{max-width:110px;border:0;background:none}
.bub .mid.fig img{max-height:110px;object-fit:contain}
.bub .doc{display:flex;align-items:center;gap:.45rem;padding:.45rem .55rem;
  text-decoration:none;color:inherit}
.bub .doc .nm{font-size:.78rem;font-weight:600;word-break:break-word;line-height:1.25}
.bub .doc .pz{font-size:.65rem;opacity:.65;font-family:var(--mono)}
.bub .mid-aviso{padding:.45rem .55rem;font-size:.73rem;line-height:1.4;opacity:.9}
/* GUARDAR: discreto de propósito. Aparece em TODA bolha de mídia, e a esmagadora
   maioria é foto que ninguém vai guardar — chamativo e repetido 200 vezes vira
   ruído, e ruído deixa de ser visto no dia do comprovante que importa. */
.bub .guardar{display:block;width:auto;margin:.3rem 0 0;padding:.1rem 0;background:none;
  border:0;color:var(--text-faint);font-size:.65rem;text-decoration:underline;
  text-underline-offset:2px;cursor:pointer;font-family:inherit}
.bub .guardar:disabled{opacity:.5;text-decoration:none;cursor:default}
.bub .guardar.ruim{color:var(--coral)}
/* o estado guardado é SELO, não botão: desguardar não existe de propósito — o
   ponto de guardar é não depender mais de ninguém, e um desfazer ao lado do
   contrato assinado é um acidente esperando data */
.bub .guardado{display:block;margin:.3rem 0 0;font-size:.65rem;color:var(--neon);opacity:.8}
.bub .mid img{cursor:zoom-in}
/* A LUPA: a foto em tela cheia por cima da conversa.
   `inset:0` + `position:fixed` cobre tudo, inclusive as barras do sistema (o _page
   pede viewport-fit=cover). Tocar em qualquer lugar fecha — num celular o alvo tem
   que ser a tela inteira, não um X de 20 pixels. */
.lupa{position:fixed;inset:0;z-index:70;background:rgba(0,0,0,.94);display:flex;
  align-items:center;justify-content:center;padding:env(safe-area-inset-top) 8px
  calc(env(safe-area-inset-bottom) + 8px)}
.lupa[hidden]{display:none}
.lupa img{max-width:100%;max-height:100%;object-fit:contain;border-radius:6px}
.lupa .fechar{position:absolute;top:calc(env(safe-area-inset-top) + 10px);right:14px;
  width:36px;height:36px;border-radius:50%;background:rgba(255,255,255,.14);color:#fff;
  border:0;font-size:1.1rem;line-height:1;display:grid;place-items:center}
/* o original abre no visualizador do próprio celular — é lá que existe pinça pra
   ampliar de verdade e "salvar imagem", que a gente não vai reimplementar */
.lupa .abrir{position:absolute;bottom:calc(env(safe-area-inset-bottom) + 14px);
  left:50%;transform:translateX(-50%);font-size:.78rem;color:#fff;opacity:.85;
  text-decoration:underline;text-underline-offset:3px}
/* a mensagem que já está na tela mas ainda não voltou do servidor */
/* A BOLHA QUE ESTÁ SUBINDO. Não é `.voando` (aquela é do texto, que recarrega a
   página): esta fica minutos na tela num vídeo de 16 MB, então precisa mostrar
   PROGRESSO em vez de só desbotar — barra parada e barra andando são a diferença
   entre "está indo" e "travou". */
.bub.subindo .mid img,.bub.subindo .mid video{opacity:.6}
.bub .barra{display:block;height:3px;border-radius:2px;background:var(--line);
  margin:.35rem 0 .1rem;overflow:hidden}
.bub .barra i{display:block;height:100%;width:0;background:var(--neon);
  transition:width .2s linear}
.bub .subiu{font-size:.62rem;opacity:.7;font-family:var(--mono)}
/* falhou: o arquivo continua no aparelho, então dá pra tentar de novo sem
   escolher tudo outra vez */
.bub.ruim{border:1px solid var(--coral-borda)}
.bub .repetir{background:none;border:0;color:var(--coral);font-size:.7rem;
  text-decoration:underline;padding:.2rem 0;width:auto;cursor:pointer}
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
/* NOVIDADES no app (migração 199, mockup novidades_tres_lugares): a faixa em
   cima da Fila aparece UMA vez por aviso e some ao tocar em "Ver" ou no ✕ —
   as duas marcam lida. Nunca fica: faixa que fica vira papel de parede. */
.faixa{margin:.2rem .8rem .5rem;border:1px solid var(--neon-borda);background:var(--neon-fundo);
  border-radius:12px;padding:.5rem .3rem .5rem .7rem;font-size:.78rem;color:var(--text);
  display:flex;align-items:center;gap:.5rem}
.faixa>a{flex:1;min-width:0;color:inherit;text-decoration:none;display:flex;gap:.45rem;align-items:center}
.faixa b{color:var(--neon-bright)}
.faixa .ver{font-weight:600;color:var(--neon-bright);white-space:nowrap;margin-left:auto}
.faixa form{margin:0}
.faixa .x{background:none;border:0;color:var(--text-faint);font-size:1rem;padding:.2rem .5rem;width:auto;margin:0;line-height:1}
.nvc{border:1px solid var(--line);border-radius:12px;background:var(--surface);padding:.7rem .8rem;
  margin-bottom:.5rem;font-size:.8rem;display:block;color:inherit;text-decoration:none}
.nvc.nova{border-left:3px solid var(--neon)}
.nvc.lida{opacity:.55}
.nvc .t{font-weight:600;font-size:.86rem;display:flex;justify-content:space-between;gap:.5rem}
.nvc .t small{font-family:var(--mono);font-weight:400;color:var(--text-faint);font-size:.66rem;white-space:nowrap}
.nvc p{margin:.25rem 0 0;color:var(--text-dim);font-size:.76rem}
.nvcorpo{white-space:pre-line;font-size:.86rem;line-height:1.5;color:var(--text)}
.tabs .tsel.ok{background:var(--neon);color:var(--ink)}
/* "este número tem outra conversa". Fica FORA do .chat de propósito: a conversa
   nasce rolada no fim (ver o script do rodapé), então um aviso no topo do
   histórico nunca seria lido por ninguém. */
.dupla{margin:.35rem 1.1rem 0;padding:.5rem .7rem;border-radius:11px;font-size:.76rem;
  background:var(--ambar-fundo);border:1px solid var(--ambar-borda);color:#F0DCA6}
.dupla b{color:var(--text)}
.dupla a{color:var(--ambar);text-decoration:underline}
/* a campanha rodando nos dois chips não é defeito: mesma faixa, tom neutro */
.dupla.info{background:var(--surface);border-color:var(--line);color:var(--text-dim)}
.dupla.info a{color:var(--neon)}
.rodape{flex-shrink:0;border-top:1px solid var(--line);background:var(--bg);padding:.7rem 1.1rem;
  padding-bottom:calc(.7rem + var(--fundo-seguro));position:relative;z-index:2}
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
  padding-bottom:calc(1.2rem + var(--fundo-seguro));
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
/* retorno do CEP. `min-height` reservado: sem isso a linha nasce e some, e o
   formulário inteiro pula pra cima e pra baixo a cada consulta. */
.cepmsg{font-size:.7rem;line-height:1.3;min-height:1.1em;color:var(--text-dim)}
.cepmsg.bom{color:var(--neon)}
.cepmsg.ruim{color:var(--ambar)}
/* o campo que o CEP trocou pisca em verde — é o que prova pro vendedor que a busca
   funcionou, principalmente quando o valor antigo já estava lá e mudou pouco. */
@keyframes fic-trocou{from{background:rgba(37,211,102,.30)}to{background:var(--bg-2)}}
.fic-c input.trocou{animation:fic-trocou 2s ease-out}
@media (prefers-reduced-motion:reduce){
  .fic-c input.trocou{animation:none;border-color:var(--neon)}
}

/* ---------- abas de baixo ---------- */
.tabs{display:flex;flex-shrink:0;border-top:1px solid var(--line);background:rgba(10,15,12,.92);
  backdrop-filter:blur(12px);padding-bottom:var(--fundo-seguro);position:relative;z-index:2}
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
/* a fileira de ESTADO usa a cor do próprio estado quando ligada — é o que faz
   "estou vendo só pré-reserva" ser óbvio sem ler o rótulo. */
.seg-ag a .c{font-family:var(--mono);font-size:.68rem;opacity:.75;margin-left:.22rem}
.seg-ag.estado a.on.res{background:var(--neon-fundo);color:var(--neon);border:1px solid var(--neon-borda)}
.seg-ag.estado a.on.pre{background:var(--ambar-fundo);color:var(--ambar);border:1px solid var(--ambar-borda)}
/* LINHA DO MÊS: é ela que faz 29 compromissos caberem no celular. */
.mes{display:flex;align-items:center;gap:.6rem;padding:.75rem 1.1rem;
  border-bottom:1px solid var(--line);background:var(--bg-2);text-decoration:none;color:var(--text)}
.mes.on{background:var(--surface)}
.mes b{font-size:.84rem;font-weight:600;flex:1}
.mes .n{font-family:var(--mono);font-size:.72rem;color:var(--text-dim);font-variant-numeric:tabular-nums}
.mes .pts{display:flex;gap:3px;flex-wrap:wrap;max-width:82px;justify-content:flex-end}
.mes .pt{width:6px;height:6px;border-radius:50%;background:var(--neon);display:block}
.mes .pt.a{background:var(--ambar)}
.mes .cho{font-size:.6rem;font-weight:700;padding:.08rem .4rem;border-radius:10px;
  border:1px solid var(--coral-borda);background:var(--coral-fundo);color:var(--coral);white-space:nowrap}
.mes .seta{color:var(--text-faint);font-size:.8rem}
.eyebrow .cnt{margin-left:auto;letter-spacing:0;font-size:.68rem;color:var(--text-dim)}
/* hora CHUTADA pelo sistema: sublinhada, nunca com a mesma cara de hora escolhida */
.vis .quando .h.sug{color:var(--coral);border-bottom:1px dotted var(--coral);font-size:.82rem}
.vis.pre{background:linear-gradient(90deg,rgba(224,163,46,.07),transparent 60%)}
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
/* o × e o valor unitário, colados no contador: "quantos × quanto" é uma leitura
   só, e separar os dois obrigaria o vendedor a procurar o preço noutro canto. */
.qtd span.u{color:var(--text-faint);min-width:0;margin-left:.15rem}
.qtd input.un{width:64px;padding:.18rem .35rem}
.avulso{display:flex;gap:.4rem;padding:0 1.1rem}
.avulso input{flex:1;min-width:0;background:var(--bg-2);border:1px solid var(--line);border-radius:10px;
  color:var(--text);padding:.55rem .7rem;font-family:inherit;font-size:.85rem}
.avulso input.v{flex:0 0 88px;font-family:var(--mono)}
/* as linhas seguintes do bloco do evento: mesma faixa, só com o respiro que a
   primeira ganha do `.secao` acima dela. */
.avulso.ev2{padding-top:.45rem}
.avulso input.hr{flex:1;font-family:var(--mono)}
.evaviso{margin:.45rem 1.1rem 0;padding:.45rem .6rem;border-radius:10px;font-size:.78rem;
 line-height:1.45;background:var(--ambar-fundo);border:1px solid var(--ambar-borda);color:var(--ambar)}
.evaviso b{display:block}
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
  padding-bottom:calc(.8rem + var(--fundo-seguro));position:relative;z-index:2}
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


def _abas(itens, ativo: str, selos: dict | None = None, verdes: tuple = ("perfil",)) -> str:
    """Barra de abas. É o que faltava no app do vendedor — ele tinha uma tela só.

    O selo da Fila é vermelho (cliente esperando); o do Perfil é verde (aviso por
    ler): cor diferente porque a urgência é outra, e um vermelho a mais na barra
    ensinaria o vendedor a ignorar o vermelho que importa."""
    selos = selos or {}
    out = []
    for chave, icone, rotulo, href in itens:
        on = " class=on" if chave == ativo else ""
        n = int(selos.get(chave) or 0)
        verde = chave in verdes
        classe = "'tsel ok'" if verde else "tsel"
        rotulo_selo = "por ler" if verde else "sem resposta"
        selo = (f"<span class={classe} aria-label='{n} {rotulo_selo}'>"
                f"{n if n < 10 else '9+'}</span>") if n else ""
        out.append(f"<a{on} href='{esc(href)}'>{_ic(icone)}{selo}"
                   f"<span>{esc(rotulo)}</span></a>")
    return "<div class=tabs>" + "".join(out) + "</div>"


def _abas_vend(ativo: str, pend: int = 0, novas: int = 0) -> str:
    return _abas([("fila", "fila", "Fila", _BASE),
                  ("agenda", "agenda", "Agenda", f"{_BASE}/agenda"),
                  ("orcamentos", "orc", "Propostas", f"{_BASE}/orcamentos"),
                  ("resultado", "resultado", "Resultado", f"{_BASE}/resultado"),
                  ("perfil", "perfil", "Perfil", f"{_BASE}/perfil")],
                 ativo, {"fila": pend, "perfil": novas})


def _novidades_vend(conta_id: int, membro_id: int) -> list[dict]:
    """Os avisos que são do VENDEDOR (pra_quem contém 'vendedor'), com o estado de
    lida dele. Best-effort, como `_pend_vend`: uma faixa não derruba a Fila."""
    try:
        from finance import novidades as nv
        return nv.listar(get_pool(), conta_id, membro_id, "vendedor")
    except Exception as e:  # noqa: BLE001
        _log.warning("novidades do vendedor %s/%s: %s: %s", conta_id, membro_id,
                     type(e).__name__, e)
        return []


def _faixa_novidade(itens: list[dict]) -> str:
    """A faixa em cima da Fila: o aviso mais novo que ele ainda não leu, um só.
    "Ver" abre o aviso (que marca lida ao abrir); o ✕ marca lida sem abrir."""
    por_ler = [n for n in itens if not n["lida"]]
    if not por_ler:
        return ""
    n = por_ler[0]
    resumo = f" {esc(n['resumo'])}" if n.get("resumo") else ""
    return (f"<div class=faixa><a href='{_BASE}/novidades/{n['id']}'>✨ "
            f"<span><b>{esc(n['titulo'])}</b>{resumo}</span><span class=ver>Ver →</span></a>"
            f"<form method=post action='{_BASE}/novidades/{n['id']}/lida'>"
            f"<input type=hidden name=volta value=fila>"
            f"<button type=submit class=x aria-label='Fechar o aviso'>✕</button></form></div>")


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
def cockpit_inicio(request: Request, meus: str = "", entrou: str = "", fora: str | None = None):
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
    return _fila(request, sess[0], sess[1], gestor=bool(g), entrou=entrou, fora=fora)


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


def _fila(request: Request, conta_id: int, membro_id: int, *, gestor: bool = False,
          entrou: str = "", fora: str | None = None) -> HTMLResponse:
    pool = get_pool()
    leads = ck.leads_do_vendedor(pool, conta_id, membro_id)
    p = ck.perfil(pool, conta_id, membro_id)
    vez = sum(1 for l in leads if l["sua_vez"])

    # a fila já tem os leads em mão: soma daqui, sem uma consulta a mais só pra aba.
    # É a carteira INTEIRA: o de fora do mês some da lista, nunca do número.
    total_pend = sum(int(l.get("pend") or 0) for l in leads)

    # O PERÍODO (mockup cockpit_mes_atual): a Fila abre no mês corrente, a escolha
    # fica na sessão, e as pílulas de fora somam quem ficou de fora à lista.
    from finance import evento_lead as _evl
    from urllib.parse import quote as _quote
    sess = request.session
    _e = (entrou or "").strip()
    if _e == "tudo" or _evl.mes_valido(_e):
        sess["ck_entrou"] = _e
    filtro_entrou = sess.get("ck_entrou") or _evl.periodo_atual()
    if fora is not None:
        sess["ck_fora"] = ",".join(x for x in (fora or "").split(",") if x in ("suavez", "festa30"))
    fora_on = [x for x in (sess.get("ck_fora") or "").split(",") if x]
    try:
        from finance import vendas as _vendas
        vende = bool(_vendas.vende_data(pool, conta_id))
    except Exception:  # noqa: BLE001
        vende = False
    fila = ck.fila_agrupada(leads, entrou=filtro_entrou, fora_on=fora_on, vende_data=vende)

    def _url(**over):
        q = {"entrou": "", "fora": None}
        q.update(over)
        partes = [f"{k}={_quote(str(v))}" for k, v in q.items() if v not in ("", None)]
        return _BASE + ("?" + "&".join(partes) if partes else "")

    pil = "".join(f"<a class='pil{' on' if m['on'] else ''}' href='{_url(entrou=m['chave'])}'>"
                  f"{esc(m['curto'])} <b>{m['n']}</b></a>" for m in fila["meses"])
    fc = fila["fora_cont"]
    if filtro_entrou != "tudo" and (fc["suavez"] or (vende and fc["festa30"])):
        def _tog(k):
            return _url(fora=",".join(sorted(set(fora_on) ^ {k})) or "")
        pil += "<span class=sep></span>"
        pil += (f"<a class='pil fora{' on' if 'suavez' in fora_on else ''}' href='{_tog('suavez')}'>"
                f"🟢 sua vez <b>{fc['suavez']}</b></a>")
        if vende:
            pil += (f"<a class='pil fora{' on' if 'festa30' in fora_on else ''}' href='{_tog('festa30')}'>"
                    f"🎉 30 dias <b>{fc['festa30']}</b></a>")
    foco = f"<div class=foco>{pil}</div>"

    def _linha_evento(l):
        """A linha do evento no card do celular, como no funil."""
        if not vende:
            return ""
        href = f"{_BASE}/lead/{l['id']}"
        if l.get("evento_em") or l.get("evento_tipo") or l.get("evento_convidados"):
            partes = []
            if l.get("evento_tipo"):
                partes.append(f"<b>{esc(l['evento_tipo'])}</b>")
            if l.get("evento_em"):
                partes.append(f"<i class=d>{esc(l['ev_data'])}</i>")
            if l.get("evento_convidados"):
                partes.append(f"{int(l['evento_convidados'])} conv.")
            src = (" <em class=src>💬 lido</em>" if l.get("evento_origem") == "conversa"
                   else " <em class=src>✓</em>" if l.get("evento_origem") == "confirmado" else "")
            return f"<span class=ev>{l['ev_ic']} " + " · ".join(partes) + src + "</span>"
        if l.get("evento_pista"):
            return (f"<span class='ev sem'>📅 {esc(l['evento_pista'])}"
                    f"<span class=perg data-href='{href}?pista=1'>confirmar</span></span>")
        return (f"<span class='ev sem'>📅 sem data"
                f"<span class=perg data-href='{href}?texto={_quote(_evl.PERGUNTA_DATA)}'>perguntar</span></span>")

    cartoes = []
    for l in leads:
        # "sua vez" = bot pausado E tem mensagem nova pra responder — não só o
        # bot estar pausado. Sem o "e tem mensagem nova", o selo ficava preso
        # em "sua vez" pra sempre depois da 1ª resposta manual (pelo app OU
        # direto no WhatsApp do celular): devolver o bot é uma ação separada
        # que ninguém clica só pra tirar o selo da tela. Quando já respondeu,
        # o selo não some — muda pra "respondido" (verde, .chip.neon), que
        # conta a história de que alguém já cuidou disso em vez de deixar o
        # card mudo.
        chip = ("<span class='chip ia'>IA</span>" if l["ia"]
                else "<span class='chip voce'>sua vez</span>" if l["sua_vez"]
                # ABERTO, SEM RESPOSTA. Pedido do dono em 28/08: ele abriu a
                # conversa, leu, e o card ficou idêntico ao de antes de abrir —
                # "sua vez" e bolinha vermelha. Selo que não muda com o que você
                # faz deixa de ser lido. Este mora entre os dois: a bolinha já
                # baixou (ele viu), mas a conversa NÃO sai da fila, porque o
                # cliente continua esperando. Vira "respondido" quando responder.
                else "<span class='chip aberto'>aberto</span>" if l["aberto"]
                else "<span class='chip neon'>respondido</span>" if l["respondido"]
                else "")
        # Quantas o cliente mandou e ninguém respondeu. O push é aviso que passa —
        # chega uma vez, e se o vendedor estiver dirigindo ou com o foco ligado,
        # passou. A bolinha fica até a conversa ser respondida, que é o que faz o
        # lead esquecido continuar visível no dia seguinte.
        pend = int(l.get("pend") or 0)
        selo = (f"<span class=pend aria-label='{pend} sem resposta'>"
                f"{pend if pend < 10 else '9+'}</span>") if pend else ""
        # de fora do mês: chega marcado com o mês em que entrou
        mes_chip = (f"<span class='chip entrou'>📥 {esc(l['entrou_rot'])}</span>" if l.get("fora") else "")
        # sem JS o card ainda é um link normal pro lead — o deslizar só acrescenta
        l["html"] = (
            f"<div class=swipe data-id='{l['id']}'>{_acoes_card(bool(l['ia']))}"
            f"<a class='lead front{' fora' if l.get('fora') else ''}' draggable=false href='{_BASE}/lead/{l['id']}'>"
            f"<span class=dot style='background:{_TEMP.get(l['temperatura'], 'var(--azul)')}'></span>"
            f"<span class=mid><span class=top><span class=emp>{esc(l['empresa'])}</span>{chip}{mes_chip}</span>"
            f"{_linha_evento(l)}"
            f"<span class=snip>{esc(l['snip'])}</span></span>{selo}</a></div>")
    # os grupos: sua vez → festa marcada → sem data → parados (dobra fechada)
    for g in fila["grupos"]:
        n = len(g["leads"])
        cabeca = f"<div class=grp>{esc(g['rotulo'])} <b>{n}</b><span class=ln></span></div>"
        corpo_g = "".join(l["html"] for l in g["leads"])
        if g["dobra"]:
            cartoes.append(f"<details class=dobra><summary>{cabeca}</summary>{corpo_g}</details>")
        else:
            cartoes.append(cabeca + corpo_g)
    if not cartoes and leads:
        # tem lead, mas nenhum no período: diz isso, em vez de "fila zerada"
        cartoes.append("<div class=vazio><div class=big>◎</div><b>Nada deste mês</b>"
                       "Toque em outro mês ou numa pílula de fora pra trazer.</div>")
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
    rot_mes = next((m["rotulo"].lower() for m in fila["meses"] if m["on"]), "tudo")
    sub = (f"{len(leads)} abertos · {vez} sua vez" if filtro_entrou == "tudo"
           else f"{fila['n_quadro']} de {len(leads)} · {rot_mes} · {vez} sua vez")
    # o aviso do vendedor (migração 199) só na fila DELE: o gestor vendo a equipe
    # recebe os dele no painel, e a faixa aqui seria de outra pessoa.
    novidades = [] if gestor else _novidades_vend(conta_id, membro_id)
    novas = sum(1 for n in novidades if not n["lida"])
    corpo = (_hdr("Meus leads", sub, inicial=_ini(p["nome"]), direita=_selo(conta_id))
             + _flash(request)
             + _faixa_novidade(novidades)
             + foco
             + f"<div class=scroll>{pushcard}{lista}{dica}{volta}</div>"
             # o "perguntar"/"confirmar" mora dentro do link do card: para o clique
             # no card e vai pra conversa com o texto (ou o aviso) pronto
             + "<script>document.querySelectorAll('.perg').forEach(function(b){"
               "b.addEventListener('click',function(e){e.preventDefault();e.stopPropagation();"
               "location.href=b.getAttribute('data-href');});});</script>"
             + (f"<a class=fab href='{_BASE}/lead/novo' aria-label='Novo lead'>+</a>"
                if not gestor else "")
             + "<div class=toast id=toast></div>"
             + _abas_vend("fila", total_pend, novas)
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


_MESES_EXT = ["", "janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
              "agosto", "setembro", "outubro", "novembro", "dezembro"]


# O JS dos dois botões. Sem regex e sem barra invertida de propósito: este arquivo
# é string Python comum (não raw), e uma classe de caractere tipo barra-d num literal
# JS vira escape inválido do Python — foi assim que um bloco <script> inteiro já
# morreu por SyntaxError aqui (ver tests/test_painel_js_sintaxe.py).
_JS_DESFECHO = """<script>
(function(){
  var caixas = document.querySelectorAll('.pend-bts');
  if(!caixas.length) return;
  Array.prototype.forEach.call(caixas, function(box){
    var id = box.getAttribute('data-ev');
    Array.prototype.forEach.call(box.querySelectorAll('.pb'), function(bt){
      bt.addEventListener('click', function(){
        var d = bt.getAttribute('data-d');
        Array.prototype.forEach.call(box.querySelectorAll('.pb'), function(b){
          b.disabled = true;
        });
        bt.textContent = 'salvando...';
        fetch('/painel/agenda/desfecho', {
          method: 'POST', credentials: 'same-origin',
          headers: {'Content-Type': 'application/x-www-form-urlencoded'},
          body: 'evento_id=' + encodeURIComponent(id) + '&desfecho=' + encodeURIComponent(d)
        }).then(function(r){ return r.json(); }).then(function(j){
          if(!j || !j.ok) throw new Error('falhou');
          var cartao = box.parentNode;
          cartao.className = 'pend feito';
          if(d === 'nao_realizado'){
            // NÃO APARECEU ABRE O REAGENDAMENTO NA HORA. O no-show que volta pra
            // lista geral é o que o dono queria parar de perder; aqui ele já sai
            // com o próximo passo na mão.
            cartao.querySelector('.pend-q').textContent = 'Marcado: nao apareceu. Reagendar?';
            box.innerHTML = '';
            var a = document.createElement('a');
            a.className = 'rea';
            a.href = AG_BASE + '/agenda/' + id + '/remarcar';
            a.textContent = 'Remarcar visita';
            box.appendChild(a);
          } else {
            cartao.querySelector('.pend-q').textContent = 'Marcado: o cliente apareceu.';
            box.innerHTML = '';
            // some sozinho — o vendedor ja respondeu, e cartao respondido em cima
            // da tela vira ruido no dia seguinte.
            window.setTimeout(function(){
              if(cartao.parentNode) cartao.parentNode.removeChild(cartao);
            }, 1200);
          }
        }).catch(function(){
          Array.prototype.forEach.call(box.querySelectorAll('.pb'), function(b){
            b.disabled = false;
          });
          bt.textContent = (d === 'realizado') ? 'Apareceu' : 'Nao apareceu';
          cartaoAviso(box);
        });
      });
    });
  });
  function cartaoAviso(box){
    var q = box.parentNode.querySelector('.pend-q');
    if(q) q.textContent = 'Nao consegui salvar. Tente de novo.';
  }
})();
</script>"""


@router.get("/cockpit/agenda", response_class=HTMLResponse)
def cockpit_agenda(request: Request, t: str = "", e: str = "", m: str = ""):
    """A agenda da CONTA, pros três papéis, DAQUI PRA FRENTE.

    O TETO DE 14 DIAS CAIU. Ele bastava quando a agenda tinha três visitas técnicas;
    com as 31 datas reais da Prime dentro, mostrava 4 de 35 compromissos e NENHUMA
    das 6 pré-reservas. O vendedor que promete data na rua não via uma única data
    segurada — o oposto do motivo desta aba existir.

    Mas tirar o teto sem dar estrutura vira um rolo de 35 linhas no celular. Então:
    os 30 primeiros dias vêm abertos (a rota da semana) e o resto vira UMA LINHA POR
    MÊS, com a contagem e uma bolinha por evento. `m=AAAA-MM` abre um mês.

    `t` é o seletor Meus × Todos — de QUEM. `e` é o seletor Tudo × Reservado ×
    Pré-reserva — O QUÊ. Duas perguntas diferentes, duas fileiras: a combinação que
    interessa a quem negocia é "minhas pré-reservas", e ela só existe com as duas.

    O padrão do `t` diz quem é: o vendedor abre em MEUS — a agenda dele é a rota do
    dia; dono e gestor abrem em TODOS, que é o trabalho deles. O dono titular não tem
    membro_id, então pra ele o seletor nem aparece."""
    sess = _sessao(request)
    g = _gerencia(request)
    if not sess and not g:
        return RedirectResponse("/cockpit/login", status_code=303)
    conta_id, membro_id = sess if sess else g
    gestao = bool(g)
    so_meus = (t == "meus") if t in ("meus", "todos") else (not gestao)
    if not membro_id:
        so_meus = False       # dono titular: "meus" não aponta pra ninguém
    estado = e if e in ("reservado", "pre") else "tudo"
    pool = get_pool()
    eventos = ck.agenda_da_conta(pool, conta_id, membro_id, so_meus=so_meus, estado=estado)
    # A VISITA QUE JÁ PASSOU E NINGUÉM RESPONDEU sobe pro topo, num bloco próprio, e
    # sai da lista normal. Antes ela simplesmente não existia aqui: a consulta cortava
    # em `inicio >= hoje` e a visita sumia da tela do vendedor no dia seguinte — por
    # isso, em 26/08, 5 das 8 visitas já realizadas da conta 34 estavam sem desfecho.
    # Sem saber quem apareceu, não há taxa de comparecimento pra gerir.
    pendentes = [v for v in eventos if v.get("precisa_resposta")]
    eventos = [v for v in eventos if not v.get("precisa_resposta")]
    cont = ck.contagem_agenda(pool, conta_id, membro_id, so_meus=so_meus)
    hoje = [v for v in eventos if v["hoje"]]

    _TAG = {"visita": ("visita", "var(--azul)", "var(--azul-borda)", "#0d1b23"),
            "pre": ("pré-reserva", "var(--ambar)", "#5a4520", "#241c0f"),
            "reservado": ("reservado", "var(--neon)", "#1e4a3a", "#10241a")}

    def bloco(v):
        acoes = []
        if v["lead_id"]:
            acoes.append(f"<a href='{_BASE}/lead/{v['lead_id']}'>{_ic('ficha', 'ic p')} Lead</a>")
        if v["maps"]:
            acoes.append(f"<a href='{esc(v['maps'])}' target=_blank rel=noopener>{_ic('mapa', 'ic p')} Mapa</a>")
        if v["zap"]:
            acoes.append(f"<a href='{esc(v['zap'])}' target=_blank rel=noopener>{_ic('zap', 'ic p')} Avisar</a>")
        # REMARCAR só em visita, e de propósito: mudar a data de uma festa mexe em
        # contrato, em sinal e às vezes na data que outro cliente queria. Fica no
        # painel, com o dono.
        if v["tipo_ev"] == "visita":
            acoes.append(f"<a class=acao-forte href='{_BASE}/agenda/{v['id']}/remarcar'>"
                         "Remarcar</a>")
            # EXCLUIR — o corretivo de "marquei com o vendedor errado". A trava real
            # (orçamento/sinal/convidado/mensagem) mora em `ag.excluir_evento`; aqui é
            # só o mesmo limite do Remarcar (só visita, só a posse).
            acoes.append(f"<a class=acao-apagar href='{_BASE}/agenda/{v['id']}/excluir'>"
                         "🗑 Excluir</a>")
        if v["ics_url"]:
            acoes.append(f"<a href='{esc(v['ics_url'])}'>{_ic('agenda', 'ic p')} Calendário</a>")
        rot, cor, borda, fundo = _TAG[v["tipo_ev"]]
        tag = (f"<span style='font-size:.62rem;font-weight:700;padding:.08rem .42rem;"
               f"border-radius:10px;border:1px solid {borda};background:{fundo};color:{cor}'>{rot}</span>")
        quem = "você" if v["minha"] else v["autor"]
        prazo = (f" <span style='color:var(--ambar)'>· sinal vence em {esc(v['prazo'])}</span>"
                 if v["prazo"] and v["prazo"] != "vencido"
                 else " <span style='color:var(--coral)'>· prazo vencido</span>" if v["prazo"] else "")
        # a hora CHUTADA pelo sistema vem sublinhada, igual ao painel: palpite não
        # pode ter a mesma cara de horário que alguém escolheu.
        cls_h = " sug" if v["hora_sugerida"] else ""
        extras = []
        if v["hora_sugerida"]:
            extras.append("horário a conferir")
        if v["choque"]:
            extras.append("<span style='color:var(--coral)'>⚠ outra festa nesta data</span>")
        linha2 = (f"<div class=loc>{' · '.join(extras)}</div>" if extras else "")
        return (f"<div class='vis{' hoje' if v['hoje'] else ''}{' pre' if v['tipo_ev'] == 'pre' else ''}'>"
                f"<div class=quando><div class='h{cls_h}'>{esc(v['hora'])}</div>"
                f"<div class=d>{esc(v['dia'])}</div></div>"
                f"<div class=mid><b>{esc(v['titulo'])}</b>"
                + (f"<div class=loc>{esc(v['local'])}</div>" if v["local"] else "")
                + f"<div class=loc>{tag}{prazo}" + (f" · {esc(quem)}" if quem else "") + "</div>"
                + linha2
                + (f"<div class=acoes>{''.join(acoes)}</div>" if acoes else "")
                + "</div></div>")

    def _q(**kw):
        """A URL preservando os outros filtros — trocar de estado não pode jogar
        fora o Meus/Todos que a pessoa escolheu."""
        d = {"t": "meus" if so_meus else "todos", "e": estado, "m": m}
        d.update(kw)
        return f"{_BASE}/agenda?" + "&".join(f"{k}={v}" for k, v in d.items() if v)

    miolo = ""
    if not eventos:
        vazio = {"reservado": "Nenhuma data reservada pra frente.",
                 "pre": "Nenhuma data segurada no momento.",
                 "tudo": "Marque uma visita pelo lead, ou toque no + pra criar um compromisso."}
        miolo = ("<div class=vazio><div class=big>◷</div><b>Nada na agenda</b>"
                 + esc(vazio[estado]) + "</div>")
    else:
        from datetime import datetime as _dt, timedelta as _td

        from finance import agenda as _ag
        # ONDE O ABERTO VIRA DOBRADO.
        #
        # O corte parte de HOJE, e não do primeiro evento: numa agenda cuja próxima
        # festa é daqui a seis meses, "primeiro + 30 dias" põe tudo em aberto e o
        # agrupamento por mês nunca aparece — que é justamente a agenda da Prime.
        #
        # E ele cai no FIM DO MÊS, não no trigésimo dia. Cortar no dia 30 parte o mês
        # no meio: quatro festas de setembro apareciam abertas em cima e uma linha
        # "setembro — 2" logo abaixo, que se lê como "setembro tem duas". O mesmo mês
        # em dois lugares diferentes é pior que uma lista um pouco mais longa.
        hoje_zero = _dt.now(_ag.BRT).replace(hour=0, minute=0, second=0, microsecond=0)
        trinta = hoje_zero + _td(days=30)
        limite = (trinta.replace(year=trinta.year + 1, month=1, day=1) if trinta.month == 12
                  else trinta.replace(month=trinta.month + 1, day=1))
        perto = [v for v in eventos if not v["hoje"] and v["inicio"] < limite]
        longe = [v for v in eventos if not v["hoje"] and v["inicio"] >= limite]

        miolo = ("<div class=eyebrow>Hoje</div>"
                 + ("".join(bloco(v) for v in hoje) if hoje
                    else "<div class=fonte style='padding:.6rem 1.1rem'>Nada marcado pra hoje.</div>"))
        if perto:
            miolo += (f"<div class='eyebrow frio'>Próximas semanas"
                      f"<span class=cnt>{len(perto)}</span></div>"
                      + "".join(bloco(v) for v in perto))
        if longe:
            # UMA LINHA POR MÊS. O mês aberto (`m`) mostra os eventos; os outros
            # mostram a contagem e uma bolinha por evento — âmbar quando é
            # pré-reserva. É o que faz 29 compromissos caberem numa tela de celular.
            miolo += f"<div class='eyebrow frio'>Mais pra frente<span class=cnt>{len(longe)}</span></div>"
            por_mes: dict[str, list] = {}
            for v in longe:
                por_mes.setdefault(v["mes"], []).append(v)
            for chave, evs in por_mes.items():
                ano, mes_n = chave.split("-")
                nome = f"{_MESES_EXT[int(mes_n)]} de {ano}"
                aberto = (m == chave)
                pts = "".join(
                    f"<i class='pt{' a' if x['tipo_ev'] == 'pre' else ''}'></i>" for x in evs[:8])
                marca = ("<span class=cho>choque de data</span>"
                         if any(x["choque"] for x in evs) else f"<span class=pts>{pts}</span>")
                miolo += (f"<a class='mes{' on' if aberto else ''}' "
                          f"href='{_q(m='' if aberto else chave)}#{chave}' id='{chave}'>"
                          f"<b>{nome}</b>{marca}<span class=n>{len(evs)}</span>"
                          f"<span class=seta>{'⌄' if aberto else '›'}</span></a>")
                if aberto:
                    miolo += "".join(bloco(v) for v in evs)

    def _cartao_pendente(v):
        """Uma visita esperando resposta: a pergunta em cima, o apoio embaixo.

        A RESPOSTA VEM PRIMEIRO, e cheia de cor — quanto menos houver pra ler, mais
        gente responde, e responder é o ponto. Mas a versão que só tinha os dois
        botões cobrava um preço escondido: ao virar pendência, a visita SAI da lista
        normal (logo acima) e leva junto os atalhos que o cartão comum tem. Quem
        quisesse remarcar precisava marcar "não apareceu" primeiro pra o botão nascer,
        e quem quisesse olhar o cliente não tinha por onde. O dono perguntou onde
        estava o remarcar olhando exatamente esta tela.

        Então entra uma segunda linha, DISCRETA (`.pb2`, sem preenchimento): ela está
        lá quando precisa e não disputa o toque com a resposta.

        As duas guardas são as mesmas do cartão normal, não regra nova:

        * `tipo_ev == "visita"` — e ela NÃO é redundante, ao contrário do que parece.
          `tipo_ev` sai de `prospeccao_id` ("visita" if r[5] else "reservado", em
          `agenda_da_conta`), enquanto `precisa_resposta` só olha o TÍTULO. Logo uma
          visita ativa, passada e SEM lead pede resposta com `tipo_ev` valendo
          "reservado" — e aí não pode oferecer remarcar, porque o cartão comum
          também não oferece. Duas telas dizendo coisas diferentes do mesmo
          compromisso é pior que as duas dizendo não. (E o motivo de a regra existir
          continua o mesmo: remarcar festa mexe em contrato, em sinal e às vezes na
          data que outro cliente queria — isso fica no painel, com o dono.)
        * `lead_id` — visita sem lead existe, e link que não abre é pior que link
          nenhum.
        """
        quando = f"{esc(v['dia'])} às {esc(v['hora'])}"
        apoio = ""
        if v["tipo_ev"] == "visita":
            apoio += (f"<a class=pb2 href='{_BASE}/agenda/{v['id']}/remarcar'>"
                      "🔁 Remarcar</a>")
        if v["lead_id"]:
            apoio += (f"<a class=pb2 href='{_BASE}/lead/{v['lead_id']}'>"
                      "👤 Abrir o lead</a>")
        return (
            "<div class=pend>"
            f"<div class=pend-top><b>{esc(v['titulo'])}</b><span>{quando}</span></div>"
            "<div class=pend-q>O cliente apareceu?</div>"
            f"<div class=pend-bts data-ev='{v['id']}'>"
            "<button type=button class='pb sim' data-d='realizado'>✅ Apareceu</button>"
            "<button type=button class='pb nao' data-d='nao_realizado'>❌ Não apareceu</button>"
            + apoio +
            "</div></div>")

    bloco_pend = ""
    if pendentes:
        bloco_pend = (
            "<div class=bloco style='margin-top:.9rem'>"
            f"<div class=pend-tit>⚠ Precisa de resposta <span class=cnt>{len(pendentes)}</span></div>"
            + "".join(_cartao_pendente(v) for v in pendentes)
            + "</div>")

    # o seletor só existe pra quem TEM agenda própria (membro_id) — pro dono
    # titular "meus" seria um filtro que devolve sempre vazio.
    seletor = ""
    if membro_id:
        seletor = (
            "<div class=bloco style='margin-top:.9rem'><div class=seg-ag>"
            + (f"<a href='{_q(t='meus', m='')}'" + (" class=on" if so_meus else "") + ">Meus</a>")
            + (f"<a href='{_q(t='todos', m='')}'" + ("" if so_meus else " class=on") + ">Todos</a>")
            + "</div></div>")
    seletor += (
        "<div class=bloco" + (" style='margin-top:-.35rem'" if membro_id else " style='margin-top:.9rem'")
        + "><div class='seg-ag estado'>"
        + (f"<a href='{_q(e='', m='')}'" + (" class=on" if estado == 'tudo' else "")
           + f">Tudo <span class=c>{cont['tudo']}</span></a>")
        + (f"<a href='{_q(e='reservado', m='')}'"
           + (" class='on res'" if estado == 'reservado' else "")
           + f">Reservado <span class=c>{cont['reservado']}</span></a>")
        + (f"<a href='{_q(e='pre', m='')}'" + (" class='on pre'" if estado == 'pre' else "")
           + f">Pré-reserva <span class=c>{cont['pre']}</span></a>")
        + "</div></div>")

    abas = _abas_dono("agenda") if gestao else _abas_vend("agenda", _pend_vend(conta_id, membro_id))
    fonte = f"{len(hoje)} hoje · {cont['reservado']} reservadas · {cont['pre']} pré-reservas"
    corpo = (_hdr("Agenda", fonte)
             + _flash(request)
             + f"<div class=scroll>{bloco_pend}{seletor}{miolo}</div>"
             + (f"<script>var AG_BASE={_BASE!r};</script>" + _JS_DESFECHO if pendentes else "")
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


@router.get("/cockpit/agenda/{ev_id}/remarcar", response_class=HTMLResponse)
def cockpit_remarcar_tela(request: Request, ev_id: int):
    """Remarcar a VISITA pelo celular — a ação que o app não tinha.

    O app só sabia CRIAR compromisso; mudar um que já existe exigia abrir o desktop.
    E é o cliente que pede pra mudar, na conversa, com o vendedor na rua.

    SÓ VISITA por ora. Mudar a data de uma festa — reservada ou segurada — mexe em
    contrato, em sinal e às vezes na data que outro cliente queria; isso fica no
    painel, com o dono. `visita_para_remarcar` devolve None pra qualquer coisa que
    não seja visita, e a tela trata igual a "não existe"."""
    sess = _sessao(request)
    g = _gerencia(request)
    if not sess and not g:
        return RedirectResponse("/cockpit/login", status_code=303)
    conta_id, membro_id = sess if sess else g
    v = ck.visita_para_remarcar(get_pool(), conta_id, membro_id, ev_id, gestao=bool(g))
    if not v:
        request.session["ck_err"] = "Essa visita não está disponível pra remarcar."
        return RedirectResponse(f"{_BASE}/agenda", status_code=303)

    aviso = ""
    if v["tem_numero"]:
        aviso = ("<label class=fic-ck><input type=checkbox name=avisar value=1 checked>"
                 f"<span><b>Avisar {esc(v['quem'])} no WhatsApp</b>"
                 "<small>a data nova sai pela conversa do Zaq e fica no histórico</small>"
                 "</span></label>")
    else:
        # sem número não há o que prometer. Uma caixinha marcada que não manda nada é
        # pior que caixinha nenhuma: o vendedor sai achando que o cliente foi avisado.
        aviso = ("<div class=fonte>Esse lead não tem WhatsApp cadastrado — vou remarcar, "
                 "mas o aviso você dá na mão.</div>")

    corpo = (_hdr("Remarcar visita", voltar=f"{_BASE}/agenda")
             + _flash(request)
             + f"<form class=telaform method=post action='{_BASE}/agenda/{ev_id}/remarcar'>"
             + "<div class=scroll><div class=secao>"
             + "<div class=agora-e><div class=r>Hoje está marcada pra</div>"
             + f"<b>{esc(v['titulo'])}</b>"
             + f"<div class=q>{esc(v['dia_sem'])}, {esc(v['quando'])}</div>"
             + (f"<div class=q>{esc(v['local'])}</div>" if v["local"] else "")
             + "</div>"
             + "<div class='fic'>"
             + "<label class='fic-c meia'><span>Nova data</span>"
               f"<input name=data type=date required value='{esc(v['data'])}'></label>"
             + "<label class='fic-c meia'><span>Hora</span>"
               f"<input name=hora type=time required value='{esc(v['hora'])}'></label>"
             + "</div>"
             + aviso
             + "<div class=fonte>A duração continua a mesma, e o convite de calendário é "
               "reemitido — o link antigo apontava pra data velha.</div>"
             + "</div></div>"
             + "<div class=rodape-b><button class=btn type=submit>Remarcar</button></div>"
             + "</form>")
    return _page("Remarcar visita", corpo)


@router.post("/cockpit/agenda/{ev_id}/remarcar")
def cockpit_remarcar(request: Request, ev_id: int, data: str = Form(""),
                     hora: str = Form(""), avisar: str = Form("")):
    """Remarca e avisa.

    O CHOQUE DE DATA vira aviso DEPOIS, e não trava antes: dois salões cabem duas
    festas e quem sabe é o dono — a mesma regra do calendário e da lista. O que não
    pode é o vendedor descobrir depois, pelo cliente."""
    sess = _sessao(request)
    g = _gerencia(request)
    if not sess and not g:
        return RedirectResponse("/cockpit/login", status_code=303)
    conta_id, membro_id = sess if sess else g
    r = ck.remarcar_visita(get_pool(), conta_id, membro_id, ev_id,
                           data=(data or "").strip(), hora=(hora or "").strip(),
                           avisar_cliente=(avisar == "1"), gestao=bool(g))
    if not r.get("ok"):
        request.session["ck_err"] = (
            "Essa visita não é sua." if r.get("erro") == "escopo"
            else r.get("erro") or "Não consegui remarcar.")
        return RedirectResponse(f"{_BASE}/agenda/{ev_id}/remarcar", status_code=303)

    msg = f"Visita remarcada pra {r['quando']} ✓"
    if r.get("avisado"):
        msg += f" — {r['quem']} foi avisado"
    elif r.get("tinha_numero"):
        msg += " — mas o aviso não saiu, fale com o cliente"

    from finance import agenda as _ag
    from datetime import datetime as _dt
    try:
        quando = _dt.fromisoformat(f"{data}T{hora}").replace(tzinfo=_ag.BRT)
        outros = ck.ocupado_no_dia(get_pool(), conta_id, quando, ignorar_id=ev_id)
    except Exception:  # noqa: BLE001 — o aviso é extra; a visita já foi remarcada
        outros = []
    if outros:
        quais = " · ".join(
            f"{o['hora']} {o['titulo']}" + (" (segurado)" if o["pre"] else "")
            for o in outros[:3])
        msg += f" ⚠ Esse dia já tinha: {quais}"

    request.session["ck_ok"] = msg
    return RedirectResponse(f"{_BASE}/agenda", status_code=303)


@router.get("/cockpit/agenda/{ev_id}/excluir", response_class=HTMLResponse)
def cockpit_excluir_tela(request: Request, ev_id: int):
    """Apagar a VISITA pelo celular — o vendedor corrige o próprio erro (marcou
    com o vendedor errado) sem precisar abrir o desktop nem chamar o dono.

    SÓ VISITA, mesmo limite do Remarcar: apagar uma festa mexe em contrato e
    sinal, isso fica no painel, com o dono. `visita_para_remarcar` já é a
    consulta certa pra mostrar ANTES de mudar nada — pede a mesma posse, só que
    aqui o "não existe" quer dizer "não dá pra apagar esta"."""
    sess = _sessao(request)
    g = _gerencia(request)
    if not sess and not g:
        return RedirectResponse("/cockpit/login", status_code=303)
    conta_id, membro_id = sess if sess else g
    v = ck.visita_para_remarcar(get_pool(), conta_id, membro_id, ev_id, gestao=bool(g))
    if not v:
        request.session["ck_err"] = "Essa visita não está disponível pra excluir."
        return RedirectResponse(f"{_BASE}/agenda", status_code=303)

    corpo = (_hdr("Excluir visita", voltar=f"{_BASE}/agenda")
             + _flash(request)
             + f"<form class=telaform method=post action='{_BASE}/agenda/{ev_id}/excluir'>"
             + "<div class=scroll><div class=secao>"
             + "<div class=agora-e><div class=r>Vai apagar de vez</div>"
             + f"<b>{esc(v['titulo'])}</b>"
             + f"<div class=q>{esc(v['dia_sem'])}, {esc(v['quando'])}</div>"
             + (f"<div class=q>{esc(v['local'])}</div>" if v["local"] else "")
             + "</div>"
             + "<div class=fonte>Isso não é cancelar — a linha some da agenda pra "
               "sempre. Se a data ainda pode acontecer, volte e cancele em vez "
               "disso. Se já tiver orçamento, sinal, convidado ou mensagem "
               "ligados a ela, não vai deixar apagar — aí é o dono quem resolve.</div>"
             + "</div></div>"
             + "<div class=rodape-b><button class='btn perigo' type=submit "
               "data-busy='⏳ Excluindo…'>Excluir de vez</button></div>"
             + "</form>")
    return _page("Excluir visita", corpo)


@router.post("/cockpit/agenda/{ev_id}/excluir")
def cockpit_excluir(request: Request, ev_id: int):
    sess = _sessao(request)
    g = _gerencia(request)
    if not sess and not g:
        return RedirectResponse("/cockpit/login", status_code=303)
    conta_id, membro_id = sess if sess else g
    r = ck.excluir_visita(get_pool(), conta_id, membro_id, ev_id, gestao=bool(g))
    if not r.get("ok"):
        erro = r.get("erro")
        if erro == "escopo":
            request.session["ck_err"] = "Essa visita não é sua."
        elif erro == "trava":
            request.session["ck_err"] = f"Não dá pra excluir: já {r['msg']}. Fala com o dono."
        else:
            request.session["ck_err"] = erro or "Não consegui excluir essa visita."
        return RedirectResponse(f"{_BASE}/agenda", status_code=303)
    request.session["ck_ok"] = "Visita excluída. Marca de novo com o vendedor certo."
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
      // o unitário fica ao lado do contador, na mesma altura — é onde o vendedor
      // olha pra conferir "quantos × quanto", e é o que o painel já mostra.
      var q=on?'<div class=qtd><button data-q="-" data-i="'+i+'" aria-label="menos">−</button>'
        +'<span>'+e.q+'</span><button data-q="+" data-i="'+i+'" aria-label="mais">+</button>'
        +'<span class=u>×</span><input class="cmp un" data-u="'+i+'" inputmode=numeric'
        +' autocomplete=off aria-label="Valor unitário" value="'+(e.u==null?(s.setup||0):e.u)+'">'
        +'</div>':'';
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
  // A QUANTIDADE É CAMPO, NÃO TEXTO. Até 01/09/2026 ela ia dentro do nome
  // ("LOCAÇÃO LEDS (× 15)") porque o snapshot do app não tinha onde guardá-la — e a
  // folha do cliente, que imprime qtd × unitário, mostrava "1 × R$ 750,00". Agora
  // `qtd` e `unitario` viajam separados, iguais aos do painel. `setup` segue sendo o
  // TOTAL da linha: é o que o funil soma e o que o fechamento cobra.
  function linhaDe(i){
    var s=O.cat[i], e=sel[i], u=(e.u==null?(s.setup||0):e.u);
    return {nome:s.nome, setup:u*e.q, mensal:(s.mensal||0)*e.q,
            qtd:e.q, unitario:u, categoria:s.categoria||"",
            desc_tipo:e.desc_tipo, desc_val:e.desc_val};
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
    if(e.target.closest(".cmp")){e.stopPropagation();return;}   // desconto e unitário
    var qb=e.target.closest("[data-q]");
    if(qb){e.stopPropagation();var i=qb.getAttribute("data-i");
      sel[i].q=Math.max(1,sel[i].q+(qb.getAttribute("data-q")==="+"?1:-1));pintaCatalogo();soma();return;}
    var row=e.target.closest(".srv");
    if(row){var j=row.getAttribute("data-i");
      if(sel[j]!==undefined)delete sel[j];
      else sel[j]={q:1,u:(O.cat[j]||{}).setup||0,desc_tipo:"pct",desc_val:0};
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
    // o unitário: mesma regra do desconto — NÃO repinta o catálogo, senão o campo
    // perde o foco a cada tecla. Só o preço da linha e o rodapé mudam.
    var fu=e.target.closest("[data-u]");
    if(fu){
      var iu=fu.getAttribute("data-u"), vu=parseInt((fu.value||"").replace(/\D/g,''),10);
      if(sel[iu]){
        sel[iu].u=Math.max(0,isNaN(vu)?0:vu);
        var ru=$("cat").querySelector('.srv[data-i="'+iu+'"]');
        if(ru)ru.querySelector(".pr").innerHTML=precoCel(linhaDe(iu));
        soma();
      }
      return;
    }
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
  // ---- o evento e as parcelas (só no nicho de eventos) ----
  var parcelas=[];
  function pintaParcelas(){
    var box=$("parcelas");if(!box)return;
    box.innerHTML=parcelas.map(function(p,i){
      return '<div class=avl><b>'+esc(p.venc||"a combinar")+'</b>'
        +'<span class=vl>'+brl(Math.round(p.valor_centavos/100))+'</span>'
        +'<button type=button data-px="'+i+'" aria-label="Remover parcela">×</button></div>';
    }).join("");
  }
  document.addEventListener("click",function(e){
    var x=e.target.closest("[data-px]");if(!x)return;
    parcelas.splice(parseInt(x.getAttribute("data-px"),10),1);pintaParcelas();
  });
  var pa=$("pcadd");
  if(pa)pa.onclick=function(){
    var v=parseInt(($("pcval").value||"").replace(/\D/g,''),10);
    if(!v){toast("Informe o valor da parcela");return;}
    parcelas.push({venc:$("pcvenc").value.trim(),valor_centavos:v*100,forma:"",obs:""});
    $("pcvenc").value="";$("pcval").value="";pintaParcelas();
  };
  // ---- reabrir uma proposta: repõe o que já estava gravado ----
  // Casar item salvo com item do catálogo é pelo NOME, que é o que o snapshot
  // guarda — ele não guarda o id do serviço. Item que não bate (avulso, ou serviço
  // apagado do catálogo depois) volta como avulso, e não some: perder uma linha ao
  // reabrir seria pior do que a proposta não abrir.
  function repor(d){
    if(!d)return;
    (d.itens||[]).forEach(function(it){
      var i=-1;
      for(var k=0;k<O.cat.length;k++){ if(O.cat[k].nome===it.nome){i=k;break;} }
      var q=Math.max(1,parseInt(it.qtd,10)||1);
      var u=parseInt(it.unitario,10)||Math.round((parseInt(it.setup,10)||0)/q);
      if(i>=0) sel[i]={q:q,u:u,desc_tipo:it.desc_tipo||"pct",desc_val:parseInt(it.desc_val,10)||0};
      else avulsos.push({nome:it.nome,setup:parseInt(it.setup,10)||0,mensal:0,
                         desc_tipo:it.desc_tipo||"pct",desc_val:parseInt(it.desc_val,10)||0});
    });
    var ev=d.evento||{}, p=function(id,v){var el=$(id);if(el&&v!=null)el.value=v;};
    // `evfim` entra aqui junto com os outros: sem ele, abrir no app um orçamento
    // que o desktop gravou com encerramento e salvar APAGAVA o encerramento — o
    // campo voltava vazio e o coletarEvento mandava vazio por cima.
    p("evdata",ev.data);p("evini",ev.inicio);p("evfim",ev.fim);p("evtipo",ev.tipo);
    p("evlocal",ev.local);p("evconv",ev.convidados);
    (d.parcelas||[]).forEach(function(x){parcelas.push(x);});
    var b=$("gerar");if(b)b.textContent="Salvar a proposta";
  }

  // A HORA QUE O SERVIDOR ENTENDE — espelho de finance/agenda._minutos, que é
  // quem transforma o orçamento em compromisso. Aceita "19", "19:00", "19h",
  // "19h30", "24:00". O que ele NÃO entende vale o mesmo que campo vazio: a data
  // não fica segurada. Só que na tela parece preenchido, e é aí que se perde a
  // data sem ninguém ver.
  // tests/test_evento_hora.py cruza esta função com a do Python.
  function horaOk(h){
    var s=String(h==null?'':h).trim().toLowerCase().replace(/h/g,':').replace(/:+$/,'');
    if(!s)return false;
    var p=s.split(':');
    if(!/^[+-]?\d+$/.test(p[0]))return false;
    if(p.length>1&&p[1]!==''&&!/^[+-]?\d+$/.test(p[1]))return false;
    var hh=parseInt(p[0],10), mm=(p.length>1&&p[1]!=='')?parseInt(p[1],10):0;
    return hh>=0&&hh<=24&&mm>=0&&mm<60;
  }
  // Duas horas escritas no mesmo campo ("18:00/23:40", "18:00 às 23:40") — o
  // improviso de quem não achou onde pôr o fim. Só age quando o Encerramento está
  // VAZIO, e o resultado aparece nos dois campos pro vendedor conferir: não é
  // adivinhação calada, é o formulário terminando de arrumar o que ele digitou.
  function horasNoTexto(t){
    var achou=String(t==null?'':t).match(/\d{1,2}\s*[:h]\s*\d{0,2}|\d{1,2}\s*h/gi)||[];
    return achou.map(function(x){return x.replace(/\s+/g,'');}).filter(horaOk);
  }
  function partirHoras(){
    var a=$("evini"), b=$("evfim");
    if(!a||!b||b.value.trim())return;
    var hs=horasNoTexto(a.value);
    if(hs.length===2&&!horaOk(a.value)){a.value=hs[0];b.value=hs[1];avisoHora();}
  }
  // AVISA, NÃO BLOQUEIA — mesma régua do desktop (web/painel_servicos): às vezes
  // se fecha a proposta com a hora ainda a combinar, e travar o botão travaria a
  // venda. O que muda aqui é que o aviso cobre os DOIS jeitos de não ter hora: em
  // branco, e escrita de um jeito que o sistema não lê.
  function avisoHora(){
    var el=$("evsemhora");if(!el)return;
    var temData=!!(($("evdata")||{}).value||'').trim();
    var ini=(($("evini")||{}).value||'').trim();
    var fim=(($("evfim")||{}).value||'').trim();
    var msg='';
    if(temData&&!ini)
      msg='<b>Sem a hora de início, esta data não entra na agenda.</b>'
        +'Pode salvar assim — mas a data só fica segurada quando você preencher o Início.';
    else if(ini&&!horaOk(ini))
      msg='<b>Não entendi o horário de início.</b>Escreva só a hora — 19:00, 19h '
        +'ou 19h30. Do jeito que está, a data não fica segurada na agenda.';
    else if(fim&&!horaOk(fim))
      msg='<b>Não entendi o horário de encerramento.</b>Escreva só a hora — 24:00, '
        +'02h ou 23h30.';
    el.innerHTML=msg;
    el.style.display=msg?'block':'none';
  }
  // Delegado no document, como o resto deste script: os campos podem nem existir
  // (fora do nicho de eventos) e amarrar ouvinte em cada um é o que o arquivo
  // evitou desde o começo.
  function _ehCampoHora(t){return t&&/^ev(data|ini|fim)$/.test(t.id||"");}
  document.addEventListener("input",function(e){if(_ehCampoHora(e.target))avisoHora();});
  document.addEventListener("change",function(e){if(_ehCampoHora(e.target))avisoHora();});
  // blur não borbulha: só chega ao document na fase de CAPTURA (o true no fim).
  document.addEventListener("blur",function(e){
    if(e.target&&e.target.id==="evini")partirHoras();
  },true);
  avisoHora();

  function coletarEvento(){
    if(!O.evento)return null;
    var v=function(id){var el=$(id);return el?el.value.trim():"";};
    var cv=parseInt((v("evconv")||"").replace(/\D/g,''),10);
    return {data:v("evdata"),inicio:v("evini"),fim:v("evfim"),tipo:v("evtipo"),
            local:v("evlocal"),convidados:isNaN(cv)?null:cv};
  }

  var g=$("gerar");
  if(g)g.onclick=function(){
    g.disabled=true;g.textContent="Gerando…";
    // manda o BRUTO e o que foi digitado: o servidor refaz a conta do desconto.
    // Mandar o já descontado faria ele descontar de novo.
    fetch(O.base+"/lead/"+O.leadId+"/orcamento",{method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({itens:itens(),desconto:{tipo:dFim.t,pct:dFim.v,valor:dFim.v},
                           evento:coletarEvento(),parcelas:parcelas,
                           orcamento_id:O.orcId||0})})
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
  repor(O.abrir);
  pintaCatalogo();pintaAvulsos();pintaParcelas();soma();
})();
</script>"""


@router.get("/cockpit/lead/{lead_id}/orcamento", response_class=HTMLResponse)
def cockpit_orcamento_montar(request: Request, lead_id: int, orc: int = 0):
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
    # O MODO VEM DO NICHO DA CONTA, como no painel — nunca do navegador. É ele que
    # decide se a tela pergunta data e parcelas, e o servidor descarta os dois em
    # quem não é evento (finance.cockpit.criar_orcamento).
    from finance import vendas as _vendas
    evento_mode = _vendas.modo_do_orcamento(pool, conta_id) == "evento"
    # `orc` reabre uma proposta existente: os itens e o evento voltam preenchidos e
    # o salvar ATUALIZA em vez de cunhar a segunda. Antes de 01/09/2026 o app só
    # sabia criar, e corrigir uma vírgula obrigava a abrir o desktop.
    reabrindo = ck.orcamento(pool, conta_id, orc, membro_id=membro_id) if orc else None
    dados = _json.dumps({"cat": cat, "leadId": lead_id, "base": _BASE,
                         "desc": ck.vende_servico(pool, conta_id),
                         "evento": evento_mode,
                         "orcId": (reabrindo or {}).get("id") or 0,
                         "abrir": reabrindo or None},
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
             # O EVENTO E AS PARCELAS. Ficam no MESMO rolo, abaixo dos itens, e não
             # numa segunda página: o vendedor está com o cliente na frente e
             # navegar entre telas no meio da conversa é onde ele desiste e volta
             # pro desktop. Só existem no nicho de eventos — nos outros o servidor
             # descarta, e mostrar campo que não grava é mentira de tela.
             + (("<div class=secao><div class=rot>O evento</div></div>"
                 "<div class=avulso><input id=evdata placeholder='Data (dd/mm/aaaa)' autocomplete=off></div>"
                 # INÍCIO E ENCERRAMENTO NA MESMA LINHA, e com o formato no
                 # placeholder. Antes só existia o Início, espremido em 88px ao lado
                 # da data — e em 01/09/2026 o vendedor da Prime, sem achar onde pôr
                 # o fim, escreveu "18:00/23:40" no Início. Três tentativas, três
                 # orçamentos (44, 45, 46), e a hora chegou no banco cortada em 10
                 # caracteres: "18:00 e en", "18:00/23:4". Um campo que não existe
                 # não faz o vendedor desistir — faz ele improvisar no campo do lado.
                 "<div class='avulso ev2'><input class=hr id=evini placeholder='Início (19:00)' autocomplete=off>"
                 "<input class=hr id=evfim placeholder='Encerramento (24:00)' autocomplete=off></div>"
                 "<div class=evaviso id=evsemhora style='display:none'></div>"
                 "<div class='avulso ev2'><input id=evtipo placeholder='Tipo (Casamento, 15 anos…)' autocomplete=off>"
                 "<input class=v id=evconv inputmode=numeric placeholder='Convid.' autocomplete=off></div>"
                 "<div class='avulso ev2'><input id=evlocal placeholder='Local' autocomplete=off></div>"
                 "<div class=secao><div class=rot>Parcelas</div></div>"
                 "<div id=parcelas></div>"
                 "<div class='avulso ev2'><input id=pcvenc placeholder='Vencimento' autocomplete=off>"
                 "<input class=v id=pcval inputmode=numeric placeholder='R$' autocomplete=off>"
                 "<button type=button id=pcadd aria-label='Adicionar parcela'>+</button></div>")
                if evento_mode else "")
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
def cockpit_orcamento_criar(request: Request, lead_id: int, payload: dict = Body(...)):
    sess = _sessao(request)
    if not sess:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    p = payload or {}
    try:
        orc_id = int(p.get("orcamento_id") or 0) or None
    except (TypeError, ValueError):
        orc_id = None
    r = ck.criar_orcamento(get_pool(), sess[0], sess[1], lead_id,
                           p.get("itens"), p.get("desconto"),
                           evento=p.get("evento"), parcelas=p.get("parcelas"),
                           orcamento_id=orc_id)
    return JSONResponse(r)


@router.post("/cockpit/lead/{lead_id}/orcamento/enviar")
def cockpit_orcamento_enviar_conversa(request: Request, lead_id: int, payload: dict = Body(...)):
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
def cockpit_visita_criar(request: Request, lead_id: int, payload: dict = Body(...)):
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
  var msg=document.getElementById('cep-msg'), ultimo='';
  function diz(t,cor){ if(msg){ msg.textContent=t||''; msg.className='cepmsg'+(cor?' '+cor:''); } }
  // Preenche E MOSTRA que preencheu. A versão anterior só tocava campo vazio, e
  // metade dos leads chega com `cidade` já preenchida da prospecção (321 de 644,
  // medido) — então o sinal mais visível de que o CEP funcionou nunca aparecia.
  // O CEP é a fonte mais confiável de cidade/UF; quem não concordar digita por cima.
  function po(id,v){
    var e=document.getElementById(id); if(!e||!v) return;
    if(e.value.trim()===v) return;                       // já era isso: nada a piscar
    e.value=v;
    e.classList.remove('trocou'); void e.offsetWidth;    // reinicia a animação
    e.classList.add('trocou');
  }
  cep.addEventListener('input',function(){
    var d=(cep.value||'').replace(/[^0-9]/g,'');
    if(d.length!==8){ ultimo=''; diz(''); return; }
    if(d===ultimo) return;                               // não repete a consulta
    ultimo=d;
    diz('buscando…');
    fetch('/api/cep/'+d).then(function(r){return r.json();}).then(function(j){
      if(!j||!j.ok){ ultimo=''; diz('CEP não encontrado — confira ou preencha à mão','ruim'); return; }
      po('fic-endereco',j.rua); po('fic-bairro',j.bairro);
      po('fic-cidade',j.cidade); po('fic-uf',j.uf);
      // CEP amplo (cidade pequena, ou CEP único de bairro grande) vem SEM rua e sem
      // bairro. Sem dizer isso, a tela parecia quebrada quando a cidade já estava lá.
      diz((!j.rua && !j.bairro)
          ? j.cidade+'/'+j.uf+' — CEP amplo, complete a rua'
          : j.cidade+'/'+j.uf+' ✓', 'bom');
      var n=document.getElementById('fic-numero'); if(n&&!n.value.trim()) n.focus();
    }).catch(function(){
      // `ultimo` volta pro vazio pra dar pra tentar de novo: engolir o erro em
      // silêncio, como era antes, é o que fazia falha e sucesso serem idênticos.
      ultimo=''; diz('não deu pra buscar agora — toque no CEP pra tentar de novo','ruim');
    });
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

    def campo(nome, rot, valor, *, tipo="text", modo="", meia=False, dica=""):
        # id=fic-<nome> porque o autopreenchimento do CEP precisa achar rua, bairro,
        # cidade e UF pra completar; sem id o JS teria que caçar por name= no form.
        # `dica` é HTML pronto (não passa por esc): hoje só o retorno do CEP usa.
        extra = f" inputmode={modo}" if modo else ""
        return (f"<label class='fic-c{' meia' if meia else ''}'><span>{esc(rot)}</span>"
                f"<input id=fic-{nome} name={nome} type={tipo}{extra} value='{esc(valor or '')}'"
                f" autocomplete=off>{dica}</label>")

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
        # a linha de retorno do CEP mora DENTRO do campo dele: em cima do teclado
        # aberto, um aviso no rodapé da tela não seria visto por ninguém.
        + campo("cep", "CEP", d.get("cep"), modo="numeric", meia=True,
                dica="<span class=cepmsg id=cep-msg></span>")
        + campo("numero", "Número", d.get("numero"), meia=True)
        + campo("endereco", "Endereço", d.get("endereco"))
        + campo("bairro", "Bairro", d.get("bairro"), meia=True)
        # <input type=date> abre o seletor nativo do celular — datilografar
        # dd/mm/aaaa numa mão, na rua, ninguém faz.
        + campo("nascimento", "Aniversário", _iso(d.get("nascimento")), tipo="date", meia=True)
        # O EVENTO (migração 197) — só onde a conta vende data: numa conta de
        # mensalidade "tipo do evento" não quer dizer nada pra ninguém.
        + ((campo("evento_tipo", "Tipo do evento", d.get("evento_tipo"))
            + campo("evento_em", "Data do evento", _iso(d.get("evento_em")), tipo="date", meia=True)
            + campo("evento_convidados", "Convidados", str(d.get("evento_convidados") or ""),
                    modo="numeric", meia=True)) if d.get("vende_data") else "")
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

    # EDITAR. O gestor vai pro painel, que tem o formulário completo (escopo, ficha
    # do cliente, margem). O VENDEDOR corrige no próprio app desde 01/09/2026 — antes
    # ele só sabia criar, e mudar uma quantidade obrigava a abrir o desktop no meio
    # da conversa com o cliente. Proposta fechada não reabre por nenhum dos dois: os
    # títulos já existem.
    if gestao:
        editar = (f"<div class=bloco><a class='btn ghost' href='/painel/servicos?abrir={orc_id}' "
                  "target=_blank rel=noopener>Editar no painel (itens, valores, escopo)</a></div>")
    elif o["lead_id"] and not fechada:
        editar = (f"<div class=bloco><a class='btn ghost' "
                  f"href='{_BASE}/lead/{o['lead_id']}/orcamento?orc={orc_id}'>"
                  "Corrigir a proposta</a></div>")
    else:
        editar = ""

    # SINAL RECEBIDO. O comprovante chega no WhatsApp do vendedor, no celular — e
    # até 01/09/2026 só o desktop tinha este botão. Ele NÃO é o que libera o
    # contrato (a assinatura soltou do sinal na mesma data); é o que firma a data na
    # agenda e lança o dinheiro. Por isso continua atrás de dois toques, como o
    # fechar: mexe em dinheiro.
    sinal = ""
    if not gestao and o.get("sinal_centavos") and not o.get("sinal_pago_em"):
        sinal = ("<div class=eyebrow>Entrada</div><div class=bloco>"
                 "<details class=fechar><summary>Sinal recebido</summary>"
                 f"<p>Confirma que a entrada de <b>{esc(_brl(o['sinal_centavos']))}</b> "
                 "caiu. A data deixa de ser provisória na agenda e o valor entra "
                 "como recebido.</p>"
                 f"<form method=post action='{_BASE}/orcamentos/{orc_id}/sinal'>"
                 "<button class=btn type=submit>Confirmar que a entrada caiu</button></form>"
                 "</details></div>")
    elif not gestao and o.get("sinal_pago_em"):
        sinal = ("<div class=eyebrow>Entrada</div><div class=bloco><div class=card "
                 "style='font-size:.84rem;color:var(--text-dim)'>"
                 "<b style='color:var(--neon)'>Sinal confirmado</b> — a data está firme "
                 "na agenda.</div></div>")

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
             + mover + sinal
             # PAGAMENTOS fica logo abaixo do sinal, e não no fim: é a pergunta
             # seguinte à que o vendedor acabou de responder ("a entrada caiu?").
             + ("<div class=eyebrow>Dinheiro</div><div class=bloco>"
                f"<a class='btn ghost' href='{_BASE}/orcamentos/{orc_id}/pagamentos'>"
                "💰 Pagamentos e comprovantes</a></div>")
             + fechar + editar
             + (f"<div class=eyebrow>O que entra</div><div class=bloco><div class=card>{itens}</div></div>"
                if itens else "")
             + (f"<div class=eyebrow>Cliente</div><div class=bloco><div class=card>{ficha_html}</div></div>"
                if ficha_html else "")
             + "</div>"
             + (_abas_dono("orcamentos") if gestao
                else _abas_vend("orcamentos", _pend_vend(conta_id, sess[1]))))
    return _page(o["titulo"], corpo)


@router.get("/cockpit/orcamentos/{orc_id}/pagamentos", response_class=HTMLResponse)
def cockpit_pagamentos(request: Request, orc_id: int):
    """O plano de pagamento no celular — e o comprovante de cada parcela.

    O BURACO QUE ISTO FECHA. Quem recebe o PIX no WhatsApp é o vendedor, em campo, e
    desde 01/09 ele já confirma o "Sinal recebido" daqui. Anexar o comprovante, não:
    era do desktop e só do dono. Ele fazia a parte difícil e deixava para trás um
    selo coral "1 parcela sem comprovante" que ninguém em campo conseguia limpar.

    SÓ O QUE JÁ FOI PAGO cobra papel — mesma régua do funil. Pedir comprovante de
    parcela que nem venceu encheria a tela de vermelho que ninguém pode resolver.
    """
    g = _gerencia(request)
    sess = _sessao(request)
    if not g and not sess:
        return RedirectResponse("/cockpit/login", status_code=303)
    conta_id = g[0] if g else sess[0]
    d = ck.pagamentos(get_pool(), conta_id, orc_id, membro_id=None if g else sess[1])
    if not d:
        request.session["ck_err"] = "Proposta não encontrada."
        return RedirectResponse(f"{_BASE}/orcamentos", status_code=303)

    linhas = []
    for p in d["parcelas"]:
        quando = _data(p["pago_em"]) if p["pago_em"] else ""
        if p["comprovante_id"]:
            selo = "<span class='pill ok'>comprovante ✓</span>"
        elif p["pago"]:
            selo = "<span class='pill falta'>sem comprovante</span>"
        else:
            selo = "<span class='pill esp'>a vencer</span>"
        sub = (f"{esc(p['forma'])} · pago {esc(quando)}" if p["pago"]
               else (f"vence {esc(_data(p['venc']))}" if p["venc"] else ""))
        # o BOTÃO SÓ NO QUE FOI PAGO, e com o rótulo dizendo qual dos dois é:
        # "trocar" num comprovante que existe evita o vendedor achar que vai
        # anexar um segundo e ficar com dois papéis pra mesma parcela.
        botao = ""
        if p["pago"] and d["pode_anexar"]:
            rot = "Trocar comprovante" if p["comprovante_id"] else "📎 Anexar comprovante"
            botao = (f"<label class='btn arq'>{rot}"
                     f"<input type=file accept='image/*,application/pdf' hidden "
                     f"data-px='{p['idx']}'></label>")
        linhas.append(
            "<div class=card style='display:flex;flex-direction:column;gap:.4rem'>"
            "<div style='display:flex;justify-content:space-between;gap:.6rem'>"
            f"<b>{esc(p['rotulo'])}</b>"
            f"<span class=vlr>{esc(_brl(p['valor_centavos']))}</span></div>"
            + (f"<div class=mut style='font-size:.78rem'>{sub}</div>" if sub else "")
            + selo + botao + "</div>")

    resumo = ("<div class=kpis style='margin-top:.9rem'>"
              f"<div class='kpi hero'><div class=v>{esc(_brl(d['recebido']))}</div>"
              f"<div class=l>Recebido</div>"
              f"<div class=d>de {esc(_brl(d['total']))} · falta {esc(_brl(d['falta']))}</div>"
              "</div></div>")
    aviso = ("" if d["pode_anexar"] else
             "<div class=bloco><div class=card style='font-size:.82rem'>"
             "Guardar arquivo não está configurado nesta conta — por isso não há "
             "botão de anexar.</div></div>")

    corpo = (_hdr("Pagamentos", "", voltar=f"{_BASE}/orcamentos/{orc_id}")
             + _flash(request) + "<div class=scroll>" + resumo + aviso
             + "<div class=eyebrow>Parcelas</div><div class=bloco>"
             + "".join(linhas) + "</div></div>"
             + _js_comprovante(orc_id)
             + (_abas_dono("orcamentos") if g
                else _abas_vend("orcamentos", _pend_vend(conta_id, sess[1]))))
    return _page("Pagamentos", corpo)


def _js_comprovante(orc_id: int) -> str:
    """O upload. CORPO BINÁRIO e nome em base64 no cabeçalho — mesmo desenho da
    rota de anexo do lead, e pelos mesmos dois motivos: multipart custa uma cópia a
    mais do arquivo, e cabeçalho HTTP é latin-1, então nome com acento tem que
    viajar codificado ou chega trocado."""
    return (
      "<script>(function(){"
      "var BASE=window.CKBASE||'/cockpit',ORC=" + str(int(orc_id)) + ";"
      "document.querySelectorAll(\"input[type=file][data-px]\").forEach(function(inp){"
      "  inp.addEventListener('change',function(){"
      "    var f=inp.files&&inp.files[0]; if(!f)return;"
      "    var lb=inp.parentNode, t0=lb.textContent;"
      "    lb.textContent='Enviando...'; lb.classList.add('on');"
      "    fetch(BASE+'/orcamentos/'+ORC+'/comprovante/'+inp.getAttribute('data-px'),{"
      "      method:'POST',body:f,"
      "      headers:{'x-nome':btoa(unescape(encodeURIComponent(f.name||'comprovante'))),"
      "               'content-type':f.type||'application/octet-stream'}})"
      "    .then(function(r){return r.json();})"
      "    .then(function(d){"
      "      if(d&&d.ok){location.reload();}"
      "      else{lb.textContent=t0;lb.classList.remove('on');"
      "           alert((d&&d.erro)||'Não deu pra anexar.');}})"
      "    .catch(function(){lb.textContent=t0;lb.classList.remove('on');"
      "           alert('Sem conexão. Tente de novo.');});"
      "  });});"
      "})();</script>")


@router.post("/cockpit/orcamentos/{orc_id}/comprovante/{parcela_idx}")
async def cockpit_comprovante(request: Request, orc_id: int, parcela_idx: int):
    """Recebe o comprovante do celular. Corpo binário; nome em base64 no cabeçalho.

    Async só pra LER o corpo — o resto (validar, subir pro bucket, registrar) vai
    pra threadpool, porque é I/O de rede que congelaria o event loop e com ele o
    painel inteiro. É o mesmo cuidado da rota de anexo, e `test_event_loop_nao_trava`
    cobra."""
    g = _gerencia(request)
    sess = _sessao(request)
    if not g and not sess:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    conta_id = g[0] if g else sess[0]
    membro_id = None if g else sess[1]
    dados = await request.body()

    def _nome() -> str:
        import base64
        try:
            return base64.b64decode(request.headers.get("x-nome", "")).decode("utf-8")
        except Exception:  # noqa: BLE001 — acento estranho não impede o anexo
            return "comprovante"

    from starlette.concurrency import run_in_threadpool
    r = await run_in_threadpool(_comprovante_sync, conta_id, membro_id, orc_id,
                                parcela_idx, dados,
                                request.headers.get("content-type", ""), _nome())
    if r.get("ok"):
        request.session["ck_ok"] = r.get("msg", "Comprovante anexado ✓")
    return JSONResponse(r, status_code=200 if r.get("ok") else 400)


def _comprovante_sync(conta_id, membro_id, orc_id, parcela_idx, dados, tipo, nome):
    """O trabalho todo fora do event loop — INCLUSIVE o `get_pool()`.

    Não é preciosismo: `tests/test_event_loop_nao_trava` procura `get_pool()` no
    corpo do handler async, e procura por texto de propósito. Deixá-lo lá, mesmo
    como argumento de `run_in_threadpool`, o avaliaria no loop — e a próxima pessoa
    a copiar esta rota copiaria o hábito junto."""
    return ck.anexar_comprovante(get_pool(), conta_id, orc_id, parcela_idx,
                                 dados, tipo, nome, membro_id=membro_id)


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


@router.post("/cockpit/orcamentos/{orc_id}/sinal")
def cockpit_orcamento_sinal(request: Request, orc_id: int):
    """Confirma o sinal recebido, do celular. Mesmo motor do funil e da agenda
    (vendas.confirmar_sinal, via ck.confirmar_sinal) — inclusive a idempotência,
    que impede o duplo-toque firmar a data duas vezes."""
    g = _gerencia(request)
    sess = _sessao(request)
    if not g and not sess:
        return RedirectResponse("/cockpit/login", status_code=303)
    conta_id = g[0] if g else sess[0]
    r = ck.confirmar_sinal(get_pool(), conta_id, orc_id,
                           membro_id=None if g else sess[1])
    if not r.get("ok"):
        request.session["ck_err"] = r.get("erro", "Não deu certo.")
        return RedirectResponse(f"{_BASE}/orcamentos/{orc_id}", status_code=303)
    # O POUSO É NA TELA DO COMPROVANTE, e é o miolo da mudança de 03/09. O
    # comprovante está na mão dele AGORA — é a mensagem de PIX que ele acabou de
    # ler no WhatsApp. Mandar de volta pra proposta o obrigaria a procurar o
    # caminho, e o funil ficava com "1 parcela sem comprovante" até alguém sentar
    # no computador. É a mesma frase que o desktop já diz depois de confirmar.
    if r.get("ja_estava"):
        request.session["ck_ok"] = r.get("msg", "Sinal já estava confirmado.")
        return RedirectResponse(f"{_BASE}/orcamentos/{orc_id}", status_code=303)
    request.session["ck_ok"] = ("Sinal confirmado ✓ — se tiver o comprovante aí, "
                                "anexa agora.")
    return RedirectResponse(f"{_BASE}/orcamentos/{orc_id}/pagamentos", status_code=303)


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


def _bloco_novidades(itens: list[dict]) -> str:
    """A seção Novidades do Perfil: o que já leu vem apagado, o que falta vem com
    a borda verde. Sem aviso nenhum, a seção não aparece — Perfil sem lista vazia."""
    if not itens:
        return ""
    cards = "".join(
        f"<a class='nvc{' lida' if n['lida'] else ' nova'}' href='{_BASE}/novidades/{n['id']}'>"
        f"<span class=t>{esc(n['titulo'])}<small>{n['publicado_em'].strftime('%d/%m')}</small></span>"
        + (f"<p>{esc(n['resumo'])}</p>" if n.get("resumo") else "") + "</a>"
        for n in itens)
    return f"<div class=eyebrow>Novidades</div><div class=bloco>{cards}</div>"


def _perfil_vendedor(request: Request, conta_id: int, membro_id: int) -> HTMLResponse:
    p = ck.perfil(get_pool(), conta_id, membro_id)
    novidades = _novidades_vend(conta_id, membro_id)

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
             + _bloco_novidades(novidades)
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
             + "</div>" + _abas_vend("perfil", _pend_vend(conta_id, membro_id),
                                     sum(1 for n in novidades if not n["lida"])))
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
# O CLIPE: escolher o arquivo, mandar, e a bolha aparecer. Raw string pela mesma
# razão do _VOZ_JS — este arquivo é template Python, e um `\n` sem a barra dobrada
# chega na página como quebra de linha de verdade e mata o script inteiro.
_ANEXO_JS = r"""
<script>
(function(){
  var BASE="__BASE__", LEAD=__LEAD__;
  var MB=1048576;
  // os MESMOS tetos do servidor (finance/cockpit._ANEXO_TETO e LIMITE_MIDIA no
  // wa-qr). Repetidos aqui pra barrar ANTES do upload: mandar 40 MB pela rede do
  // celular pra ouvir "grande demais" no fim gasta o pacote de dados do vendedor
  // e um minuto da vida dele. O servidor barra de novo — tela não é fonte confiável.
  var TETO={imagem:5*MB, video:32*MB, documento:16*MB};
  function $(id){return document.getElementById(id);}
  var clipe=$("clipe"), arq=$("arq"), comp=$("comp");
  var chat=document.querySelector(".chat");
  if(!clipe||!arq) return;

  function tipoDe(m){
    m=(m||"").split(";")[0].toLowerCase();
    if(m.indexOf("image/")===0) return "imagem";
    if(m.indexOf("video/")===0) return "video";
    return "documento";
  }
  function b64(txt){
    // btoa só engole latin-1, e nome de arquivo brasileiro tem acento
    return btoa(String.fromCharCode.apply(null, new TextEncoder().encode(txt||"")));
  }
  function esc(t){var e=document.createElement("div");e.textContent=t||"";return e.innerHTML;}
  function tam(b){
    if(b>=MB) return (b/MB).toFixed(1).replace(".",",")+" MB";
    if(b>=1024) return Math.round(b/1024)+" KB";
    return b+" B";
  }
  function fim(){ if(chat) chat.scrollTop=chat.scrollHeight; }

  // ---------------------------------------------------------------- a bolha
  // Ela nasce ANTES do upload começar, já com a imagem que o vendedor escolheu.
  // O `createObjectURL` aponta pro arquivo no APARELHO: aparece instantâneo, não
  // custa download nenhum, e continua servindo depois que o envio termina — a foto
  // que ele mandou nunca precisa ser baixada de volta.
  function bolha(f, tipo, legenda){
    var d=document.createElement("div");
    d.className="bub out subindo";
    var url=(tipo==="documento")?null:URL.createObjectURL(f);
    var corpo="";
    if(tipo==="imagem") corpo='<span class=mid><img src="'+url+'" alt=""></span>';
    else if(tipo==="video") corpo='<span class=mid><video src="'+url+'" muted playsinline></video></span>';
    else corpo='<span class="mid doc"><span>📄</span><span><span class=nm>'+esc(f.name)
               +'</span><br><span class=pz>'+tam(f.size)+'</span></span></span>';
    d.innerHTML='<div class=who>Você</div>'+corpo+(legenda?esc(legenda):"")
      +'<span class=barra><i></i></span><span class=subiu>enviando… 0%</span>';
    if(chat){ chat.appendChild(d); fim(); }
    d.__url=url;
    return d;
  }
  function soltar(d){
    // devolve a memória do preview. Num vídeo de 16 MB isso não é detalhe.
    if(d && d.__url){ try{ URL.revokeObjectURL(d.__url); }catch(e){} d.__url=null; }
  }

  // ---------------------------------------------------------------- o envio
  function subir(f, tipo, legenda, d){
    var barra=d.querySelector(".barra i"), rot=d.querySelector(".subiu");
    var xhr=new XMLHttpRequest();
    xhr.open("POST", BASE+"/lead/"+LEAD+"/anexo");
    xhr.setRequestHeader("Content-Type", f.type || "application/octet-stream");
    xhr.setRequestHeader("X-Nome", b64(f.name));
    xhr.setRequestHeader("X-Legenda", b64(legenda));
    xhr.upload.onprogress=function(e){
      if(!e.lengthComputable) return;
      var pct=Math.round(e.loaded*100/e.total);
      if(barra) barra.style.width=pct+"%";
      // 100% do UPLOAD não é fim: o servidor ainda espera o WhatsApp cifrar e
      // receber o arquivo. Dizer "100%" e ficar parado parece travado — daí o
      // "quase lá", que é honesto sobre o que está acontecendo.
      if(rot) rot.textContent = pct>=100 ? "quase lá…" : ("enviando… "+pct+"%");
    };
    xhr.onload=function(){
      var j=null;
      try{ j=JSON.parse(xhr.responseText||"{}"); }catch(e){ j=null; }
      if(j && j.ok){
        d.classList.remove("subindo");
        var b=d.querySelector(".barra"); if(b) b.remove();
        if(rot) rot.remove();
        d.setAttribute("data-id", j.id);
        // avisa o polling que esta mensagem já está na tela, senão ela voltaria
        // pela outra ponta e o vendedor veria a mesma foto duas vezes
        if(window.__viu) window.__viu(j.id);
        return;
      }
      falhou(d, f, tipo, legenda, (j && j.erro) || "Não consegui enviar.");
    };
    xhr.onerror=function(){ falhou(d, f, tipo, legenda, "Falha de conexão."); };
    // O ARQUIVO VAI DIRETO, sem `arrayBuffer()`. Lendo antes, os 16 MB inteiros
    // iam pra memória do navegador — no celular do vendedor isso é o que trava a
    // tela. Passando o File, o navegador lê do disco enquanto sobe.
    xhr.send(f);
  }

  function falhou(d, f, tipo, legenda, msg){
    d.classList.remove("subindo"); d.classList.add("ruim");
    var b=d.querySelector(".barra"); if(b) b.remove();
    var rot=d.querySelector(".subiu");
    if(rot){ rot.textContent=msg+" "; }
    var bt=document.createElement("button");
    bt.type="button"; bt.className="repetir"; bt.textContent="tentar de novo";
    bt.onclick=function(){
      // o arquivo continua no aparelho: dá pra repetir sem escolher tudo de novo
      bt.remove();
      d.classList.remove("ruim"); d.classList.add("subindo");
      if(rot) rot.textContent="enviando… 0%";
      var nb=document.createElement("span"); nb.className="barra";
      nb.innerHTML="<i></i>";
      d.insertBefore(nb, rot);
      subir(f, tipo, legenda, d);
    };
    if(rot) rot.appendChild(bt); else d.appendChild(bt);
  }

  clipe.onclick=function(){ arq.value=""; arq.click(); };

  arq.onchange=function(){
    var f=arq.files && arq.files[0];
    arq.value="";                       // já libera pra escolher o próximo
    if(!f) return;
    var t=tipoDe(f.type);
    if(f.size>TETO[t]){
      // recado NA CONVERSA, não em janela: `alert` é modal, trava a página e o
      // vendedor não consegue nem digitar enquanto ela está aberta.
      //
      // E o recado diz O TAMANHO DO ARQUIVO, não só o limite. A primeira versão
      // dizia "passa de 16 MB" e pronto: quem lia não sabia se tinha passado por
      // pouco ou pelo dobro, nem o que fazer. Sabendo os dois números, dá pra
      // decidir na hora se vale mandar um trecho menor.
      var e=document.createElement("div");
      e.className="bub out ruim";
      e.innerHTML='<div class=who>Você</div><b>'+esc(f.name)+'</b>'
        +'<span class=subiu>não enviado · '+tam(f.size)
        +' — o limite é '+(TETO[t]/MB)+' MB</span>';
      if(chat){ chat.appendChild(e); fim(); }
      return;
    }
    // a legenda é o que já estiver escrito na caixa: no WhatsApp ela chega colada
    // na foto, que é como as pessoas mandam de verdade
    var cx=comp && comp.querySelector("input[name=texto]");
    var legenda=(cx && cx.value || "").trim();
    if(cx) cx.value="";
    // O CLIPE NÃO É DESABILITADO e a caixa de texto segue livre: o upload corre por
    // baixo, e o vendedor continua conversando. Era isto que a janela impedia.
    subir(f, t, legenda, bolha(f, t, legenda));
  };
})();
</script>
"""


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



def _tam_br(b) -> str:
    b = int(b or 0)
    if b >= 1048576:
        return ("%.1f" % (b / 1048576)).replace(".", ",") + " MB"
    if b >= 1024:
        return "%d KB" % round(b / 1024)
    return "%d B" % b


def _midia_html(lead_id: int, m: dict) -> str:
    """A bolha de mídia, montada no SERVIDOR pra primeira carga.

    O arquivo não está no nosso disco: o `src` aponta pra rota que busca no CDN do
    WhatsApp e decifra na hora (finance/wa_midia.py). `loading=lazy` e
    `preload=none` são o que faz o custo ser zero pra foto que ninguém abre — numa
    conversa de 200 mensagens o navegador carrega as poucas que aparecem na tela.

    Existe em DUAS cópias, esta e a `mid()` do JS, porque o Cockpit desenha a
    conversa duas vezes: aqui na primeira carga e lá no polling que traz o que
    chegou depois. As duas têm que produzir o mesmo HTML.
    """
    d = m.get("midia") or {}
    tipo = d.get("tipo") or ""
    mid = m.get("id") or 0
    if not tipo or not mid:
        return ""
    src = f"{_BASE}/lead/{lead_id}/midia/{mid}"
    guardar = _guardar_html(mid, d)
    if tipo == "documento":
        peso = f"<br><span class=pz>{esc(_tam_br(d.get('bytes')))}</span>" if d.get("bytes") else ""
        return (f"<a class='mid doc' href='{esc(src)}' target=_blank rel=noopener>"
                f"<span>📄</span><span><span class=nm>{esc(d.get('nome') or 'arquivo')}"
                f"</span>{peso}</span></a>{guardar}")
    if tipo == "video":
        return (f"<span class=mid><video controls preload=none playsinline "
                f"src='{esc(src)}'></video></span>{guardar}")
    fig = " fig" if tipo == "figurinha" else ""
    return (f"<span class='mid{fig}'><img loading=lazy src='{esc(src)}' alt='' "
            f"onerror=\"this.parentNode.innerHTML='<span class=mid-aviso>🖼 não consegui "
            f"carregar</span>'\"></span>{guardar}")


def _guardar_html(mensagem_id: int, d: dict) -> str:
    """O botão Guardar da bolha — ou o selo, quando já está guardado.

    DISCRETO DE PROPÓSITO. Ele aparece em TODA bolha de mídia, e a esmagadora
    maioria delas é foto que ninguém vai guardar. Um botão chamativo repetido 200
    vezes numa conversa vira ruído, e ruído deixa de ser visto justamente no dia em
    que chega o comprovante que importa.

    O estado guardado NÃO é um botão: é um selo, sem ação. Desguardar não existe de
    propósito — o ponto de guardar é o arquivo não depender mais de ninguém, e um
    botão de desfazer ao lado do contrato assinado é um acidente esperando data.
    """
    if not mensagem_id:
        return ""
    if d.get("guardada"):
        return "<span class=guardado title='Guardado no Zaq'>🔒 guardado</span>"
    return (f"<button type=button class=guardar data-msg='{mensagem_id}'>"
            f"guardar</button>")


def _hora_br(dt) -> str:
    """HH:MM em horário de Brasília — a hora que vai na bolha, como no WhatsApp.

    Sem fuso a mensagem das 20:28 apareceria como 23:28: o banco guarda em UTC, e
    é o vendedor de Teresina que lê isso no meio de um atendimento.
    """
    if not hasattr(dt, "strftime"):
        return ""
    from finance import agenda as ag
    try:
        return dt.astimezone(ag.BRT).strftime("%H:%M")
    except (ValueError, TypeError):
        return dt.strftime("%H:%M")


def _dia_br(dt) -> str:
    """A tarja que separa os dias: HOJE, ONTEM ou a data.

    O WhatsApp tem isso e a gente não tinha, e é o que impede a conversa de virar
    uma fila sem tempo — sem a tarja, "20:28" pode ser hoje ou de três semanas
    atrás, e o vendedor não tem como saber sem abrir o WhatsApp do celular.
    """
    if not hasattr(dt, "strftime"):
        return ""
    from datetime import datetime
    from finance import agenda as ag
    try:
        d = dt.astimezone(ag.BRT).date()
        hoje = datetime.now(ag.BRT).date()
    except (ValueError, TypeError):
        return ""
    if d == hoje:
        return "HOJE"
    if (hoje - d).days == 1:
        return "ONTEM"
    return d.strftime("%d/%m/%Y")


def _lead_vendedor(request: Request, lead_id: int, d: dict,
                   pode_voz: bool = False, saida_wa: bool = True) -> HTMLResponse:
    sub = " · ".join(x for x in [d.get("cidade") or "", d.get("uf") or ""] if x) or (d.get("doc_fmt") or "")
    # o evento na frente de tudo: é o que se precisa ver antes de responder (197)
    if d.get("evento_fmt"):
        sub = d["evento_fmt"] + (" · " + sub if sub else "")

    bolhas = []
    # (o _midia_html mora fora daqui pra o polling do JS desenhar igual — ver cxMid)
    dia_atual = ""
    for m in d["mensagens"]:
        who = m["who"]
        rot = ("<div class=who>Agente</div>" if who == "ia"
               else "<div class=who>Você</div>" if who == "out" else "")
        # A TARJA DO DIA, igual à do WhatsApp: só entra quando o dia VIRA. Repetida
        # em toda bolha ela viraria ruído; sem ela, "20:28" pode ser hoje ou de três
        # semanas atrás e não há como saber sem abrir o celular.
        dia = _dia_br(m.get("quando"))
        if dia and dia != dia_atual:
            bolhas.append(f"<div class=diadia>{esc(dia)}</div>")
            dia_atual = dia
        hora = _hora_br(m.get("quando"))
        selo = f"<span class=hora>{esc(hora)}</span>" if hora else ""
        bolhas.append(f"<div class='bub {esc(who)}' data-id='{m.get('id') or 0}'>"
                      f"{rot}{_midia_html(lead_id, m)}{esc(m['texto'])}{selo}</div>")
    if d["ia"]:
        bolhas.insert(0, "<div class=aviso>O agente está atendendo. Toque em "
                         "<b>Assumir</b> pra responder você.</div>")
    chat = "".join(bolhas) or "<div class=aviso>Sem mensagens ainda.</div>"
    # A lupa nasce escondida e vazia: o `src` só é preenchido quando alguém toca numa
    # foto, então ela não custa um download a mais só por existir na página.
    #
    # FORA do `.chat` de propósito. O `.chat` tem `position:relative;z-index:1`, e
    # isso cria contexto de empilhamento: um `position:fixed` lá dentro fica preso
    # nele e pode ser pintado por baixo dos irmãos, por mais alto que seja o z-index.
    lupa_html = ("<div class=lupa id=lupa hidden onclick='lupaFecha(event)'>"
                 "<button type=button class=fechar aria-label=Fechar>✕</button>"
                 "<img id=lupaImg alt=''>"
                 "<a class=abrir id=lupaAbrir target=_blank rel=noopener>abrir original ↗</a>"
                 "</div>")

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
        # O CLIPE. Mesmo portão do microfone (`pode_voz` = canal QR): é a mesma
        # rota do serviço Node por baixo, e oferecer o botão numa conta que não
        # manda faria o vendedor escolher o arquivo, esperar, e receber erro.
        # `accept` aberto de propósito — PDF, planilha e comprovante são o que ele
        # mais precisa mandar, e uma lista de tipos envelhece contra o vendedor.
        clipe = ("<button type=button class=mic id=clipe aria-label='Anexar arquivo'>"
                 "<svg width=20 height=20 viewBox='0 0 24 24' fill=none stroke=currentColor "
                 "stroke-width=1.8 stroke-linecap=round stroke-linejoin=round>"
                 "<path d='M21.4 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.2-9.19a4 4 0 "
                 "015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48'/></svg></button>"
                 "<input type=file id=arq hidden>") if pode_voz else ""
        # "perguntar" da Fila: a pergunta chega pronta na caixa, o vendedor confere e manda
        _qp = getattr(request, "query_params", None)
        texto_pre = ((_qp.get("texto") if _qp else "") or "")[:300]
        acao = (f"<form class=composer id=comp method=post action='{_BASE}/lead/{lead_id}/mensagem'>"
                f"<input name=texto placeholder='Responder…' required autocomplete=off value='{esc(texto_pre)}'"
                f"{' autofocus' if texto_pre else ''}>"
                + clipe + mic +
                "<button type=submit aria-label=Enviar>&#10148;</button>"
                + barra + "</form>")
        if pode_voz:
            acao += _VOZ_JS.replace("__BASE__", _BASE).replace("__LEAD__", str(lead_id))
            acao += _ANEXO_JS.replace("__BASE__", _BASE).replace("__LEAD__", str(lead_id))

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
           # a última tarja JÁ desenhada pelo servidor — sem ler ela daqui, a
           # primeira mensagem do polling repetiria "HOJE" logo abaixo de um "HOJE"
           "var tarjas=chat.querySelectorAll('.diadia');"
           "var diaAtual=tarjas.length?tarjas[tarjas.length-1].textContent:'';"
           "function rot(w){return w==='ia'?'<div class=who>Agente</div>':"
           "w==='out'?'<div class=who>Você</div>':'';}"
           "function txt(s){var e=document.createElement('div');e.textContent=s;return e.innerHTML;}"
           # A LUPA. Delegação no chat inteiro, e não um onclick por imagem: as fotos
           # que chegam pelo polling nascem depois desta linha rodar, e um handler
           # amarrado na criação não pegaria elas.
           "var lupa=document.getElementById('lupa');"
           "if(chat&&lupa){chat.addEventListener('click',function(e){"
           "var im=e.target;"
           "if(!im||im.tagName!=='IMG'||!im.closest('.mid'))return;"
           "document.getElementById('lupaImg').src=im.getAttribute('src');"
           "document.getElementById('lupaAbrir').href=im.getAttribute('src');"
           "lupa.hidden=false;});}"
           # tocar em qualquer lugar fecha, MENOS no link de abrir o original — que
           # está por cima do fundo e seria engolido pelo mesmo clique
           # no `window` porque quem chama é o onclick do HTML, que está FORA
           # deste IIFE — declarada só aqui dentro, ela não existiria pra ele
           "window.lupaFecha=function(e){"
           "if(e&&e.target&&e.target.id==='lupaAbrir')return;"
           "var l=document.getElementById('lupa');if(!l)return;"
           "l.hidden=true;document.getElementById('lupaImg').removeAttribute('src');};"
           "document.addEventListener('keydown',function(e){"
           "if(e.key==='Escape')lupaFecha();});"
           # A MESMA bolha de mídia do _midia_html, pro que chega pelo polling. O
           # arquivo não está no nosso disco: o src busca no CDN e decifra na hora, e
           # o loading=lazy faz a foto que ninguém abre não custar nada.
           "function tam(b){b=Number(b)||0;"
           "if(b>=1048576)return (b/1048576).toFixed(1).replace('.',',')+' MB';"
           "if(b>=1024)return Math.round(b/1024)+' KB';return b+' B';}"
           "function mid(m){var d=m.midia;if(!d||!d.tipo||!m.id)return '';"
           f"var s='{_BASE}/lead/{lead_id}/midia/'+m.id;"
           # o mesmo botão do _guardar_html — as duas cópias têm que dar o mesmo HTML
           "var g=d.guardada?'<span class=guardado>🔒 guardado</span>':"
           "('<button type=button class=guardar data-msg=\"'+m.id+'\">guardar</button>');"
           "if(d.tipo==='documento')return '<a class=\"mid doc\" href=\"'+s+'\" target=_blank "
           "rel=noopener><span>📄</span><span><span class=nm>'+txt(d.nome||'arquivo')+'</span>'"
           "+(d.bytes?('<br><span class=pz>'+tam(d.bytes)+'</span>'):'')+'</span></a>'+g;"
           "if(d.tipo==='video')return '<span class=mid><video controls preload=none "
           "playsinline src=\"'+s+'\"></video></span>'+g;"
           "return '<span class=\"mid'+(d.tipo==='figurinha'?' fig':'')+'\">"
           "<img loading=lazy src=\"'+s+'\" alt=\"\"></span>'+g;}"
           # GUARDAR: delegação no chat, como a lupa — as bolhas do polling nascem
           # depois desta linha rodar, e handler amarrado na criação não pegaria elas.
           "chat.addEventListener('click',function(e){"
           "var b=e.target.closest&&e.target.closest('.guardar');if(!b)return;"
           "var id=b.getAttribute('data-msg');if(!id||b.disabled)return;"
           "b.disabled=true;b.textContent='guardando…';"
           f"fetch('{_BASE}/lead/{lead_id}/guardar/'+id,{{method:'POST'}})"
           ".then(function(r){return r.json();}).then(function(j){"
           # vira SELO, não volta a ser botão: guardado é estado final
           "if(j&&j.ok){var s2=document.createElement('span');s2.className='guardado';"
           "s2.textContent='🔒 guardado';b.parentNode.replaceChild(s2,b);return;}"
           "b.disabled=false;b.textContent='guardar';"
           "b.classList.add('ruim');b.title=(j&&j.erro)||'não consegui guardar';"
           "}).catch(function(){b.disabled=false;b.textContent='guardar';});});"
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
           # a tarja do dia também no polling: quem deixa a conversa aberta e vira
           # a meia-noite veria as duas datas coladas sem isto
           "if(m.dia&&m.dia!==diaAtual){var sep=document.createElement('div');"
           "sep.className='diadia';sep.textContent=m.dia;chat.appendChild(sep);"
           "diaAtual=m.dia;}"
           "var d=document.createElement('div');d.className='bub '+m.who;"
           "d.setAttribute('data-id',m.id);d.innerHTML=rot(m.who)+mid(m)+txt(m.texto)"
           "+(m.hora?('<span class=hora>'+txt(m.hora)+'</span>'):'');"
           "chat.appendChild(d);ultimo=m.id;});"
           "if(perto)fim();"
           "}).catch(function(){ocupado=false;});}"
           # exposto pra quem manda áudio puxar na hora: recarregar a página inteira
           # depois de enviar dá ~1s de tela branca num aparelho de vendedor
           "window.__puxa=puxa;setInterval(puxa,8000);"
           # quem já pôs a mensagem na tela por conta própria (o anexo, que desenha
           # a bolha com a foto local antes mesmo de o upload acabar) avisa aqui.
           # Sem isso o polling traria a MESMA mensagem de novo, e o vendedor veria
           # a foto que acabou de mandar duas vezes.
           "window.__viu=function(id){id=Number(id)||0;if(id>ultimo)ultimo=id;};"
           "document.addEventListener('visibilitychange',function(){"
           "if(document.visibilityState==='visible')puxa();});"
           "})();</script>")

    # O mesmo número com conversa em outra ficha. Sai ANTES do chat porque a conversa
    # nasce rolada no fim; e sai como faixa, não como bolha, pra não parecer mensagem
    # de ninguém. Ver `aviso_outra_conversa` e `outras_conversas_do_numero`.
    # E o TOM muda conforme o caso: mesmo chip é a entrega dupla (defeito, âmbar);
    # chip diferente é a campanha rodando nos dois números da empresa, que é de
    # propósito — ali o vendedor precisa saber, não se assustar.
    av = d.get("aviso_conversa") or {}
    dupla = ""
    if av.get("texto"):
        link = (f" <a href='{_BASE}/lead/{av['lead_id']}'>abrir</a>"
                if av.get("lead_id") else "")
        rot = "<b>Atenção:</b> " if av.get("defeito") else ""
        cls = "dupla" if av.get("defeito") else "dupla info"
        dupla = (f"<div class='{cls}'>{rot}{esc(av['texto'])}"
                 f" O histórico dela não aparece aqui.{link}</div>")

    chip = ("<span class='chip ia'>IA</span>" if d["ia"] else "<span class='chip voce'>você</span>")
    # A PISTA (198): o leitor ouviu o mês (ou uma data diferente) e não gravou. O
    # aviso fica em cima do chat até o vendedor confirmar na ficha ou perguntar o dia.
    pista = ""
    if d.get("evento_pista"):
        from finance import evento_lead as _evl
        from urllib.parse import quote as _quote
        pista = (f"<div class='aviso pista'>💬 O cliente <b>{esc(d['evento_pista'])}</b> na conversa. "
                 "Confirmar a data?<span class=bts>"
                 f"<a class='bt ok' href='{_BASE}/lead/{lead_id}/ficha'>Abrir a ficha</a>"
                 f"<a class=bt href='{_BASE}/lead/{lead_id}?texto={_quote(_evl.PERGUNTA_DATA)}'>Perguntar o dia</a>"
                 "</span></div>")
    corpo = (
               _hdr(d["empresa"], sub, voltar=_BASE, direita=chip)
             + _flash(request)
             + dupla + pista
             + f"<div class=chat>{chat}</div>{lupa_html}"
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
    # o evento na frente de tudo: é o que se precisa ver antes de responder (197)
    if d.get("evento_fmt"):
        sub = d["evento_fmt"] + (" · " + sub if sub else "")
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


def _anexo_sync(conta_id, membro_id, lead_id, dados, nome, mime, legenda):
    """O trabalho de verdade do anexo — síncrono, fora do event loop.

    Mora aqui fora e não dentro do handler de propósito: o `get_pool()` no corpo de
    um `async def` é justamente o que `tests/test_event_loop_nao_trava.py` procura,
    e a busca dele é textual porque a alternativa — adivinhar o que é bloqueante —
    erraria. Mesmo desenho do `_webhook_wa_qr_saida_sync`.
    """
    return ck.enviar_anexo(get_pool(), conta_id, membro_id, lead_id, dados,
                           nome=nome, mimetype=mime, legenda=legenda)


def _guardar_sync(conta_id, membro_id, lead_id, mensagem_id):
    """O trabalho do Guardar, fora do event loop.

    Mora aqui fora pelo mesmo motivo do `_anexo_sync`: `get_pool()` no corpo de um
    `async def` é o que o tests/test_event_loop_nao_trava.py procura. E aqui pesa
    mais — guardar BAIXA do CDN e SOBE pro bucket, dois saltos de rede em série."""
    return ck.guardar_midia(get_pool(), conta_id, membro_id, lead_id, mensagem_id)


@router.post("/cockpit/lead/{lead_id}/guardar/{mensagem_id}")
async def cockpit_guardar(request: Request, lead_id: int, mensagem_id: int):
    """Guardar este arquivo de vez (passo 5).

    O CDN do WhatsApp expira. Para foto de decoração tudo bem — pede de novo. Para
    o comprovante do sinal e o contrato assinado, não: o dia em que se precisa
    deles é o dia da discussão, meses depois. Este botão copia o arquivo pro nosso
    bucket privado, e a partir daí a bolha serve de lá.
    """
    sess = _sessao(request)
    if not sess:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    r = await run_in_threadpool(_guardar_sync, sess[0], sess[1], lead_id, mensagem_id)
    return JSONResponse(r)


@router.post("/cockpit/lead/{lead_id}/anexo")
async def cockpit_anexo(request: Request, lead_id: int):
    """O vendedor mandando foto, vídeo ou documento de dentro do Zaq (passo 4).

    CORPO BINÁRIO, não multipart. O arquivo já vem do `<input type=file>` e não há
    mais nada pra empacotar junto; multipart custaria uma cópia a mais dos dois
    lados e é a cópia que importa quando o arquivo tem 16 MB. É o mesmo desenho da
    rota de voz, pelo mesmo motivo.

    O NOME VIAJA EM BASE64 no cabeçalho `x-nome`. Cabeçalho HTTP é latin-1 por
    especificação, e nome de arquivo brasileiro tem acento — mandar cru dá erro de
    codificação no meio do caminho, ou pior, chega trocado e o cliente recebe um
    PDF chamado "OrÃ§amento".

    O TRABALHO VAI PRA THREADPOOL, e só a leitura do corpo fica no async. Aqui isto
    é mais sério que nos webhooks: o envio espera o Baileys CIFRAR e SUBIR até 16 MB
    pro WhatsApp — dezenas de segundos. Rodando no event loop, congelaria o worker
    inteiro (painel incluso) o tempo todo, e agora são DOIS workers, não quatro. É o
    mesmo defeito que em 22/08/2026 levou a resposta de 527 ms pra ~50 s com a CPU
    em 0,7%: esperando, não trabalhando. `tests/test_event_loop_nao_trava.py` cobra.
    """
    sess = _sessao(request)
    if not sess:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    dados = await request.body()
    if not dados:
        return JSONResponse({"ok": False, "erro": "Arquivo vazio."})

    def _texto(cab: str) -> str:
        """Cabeçalho base64 → str. Sem nome o documento ainda vai, como 'arquivo':
        um acento estranho não pode impedir o vendedor de mandar o orçamento."""
        try:
            import base64
            return base64.b64decode(request.headers.get(cab) or "").decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            return ""

    nome, legenda = _texto("x-nome"), _texto("x-legenda")
    mime = (request.headers.get("content-type") or "").split(";")[0].strip()
    r = await run_in_threadpool(_anexo_sync, sess[0], sess[1], lead_id,
                                dados, nome, mime, legenda)
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
    # `hora` e `dia` vão prontos do servidor: o fuso é resolvido AQUI, e não no
    # navegador. O celular do vendedor pode estar em qualquer fuso (ou com a hora
    # errada), e a conversa tem que mostrar o mesmo horário pra todo mundo.
    novas = [{"id": m["id"], "who": m["who"], "texto": m["texto"],
              "hora": _hora_br(m.get("quando")), "dia": _dia_br(m.get("quando")),
              **({"midia": m["midia"]} if m.get("midia") else {})}
             for m in d["mensagens"] if (m.get("id") or 0) > desde]
    return JSONResponse({"ok": True, "ia": bool(d["ia"]), "msgs": novas})


@router.get("/cockpit/lead/{lead_id}/midia/{mensagem_id}")
def cockpit_midia(request: Request, lead_id: int, mensagem_id: int):
    """A mídia de uma mensagem da conversa deste lead.

    ROTA PRÓPRIA, e não a do painel de prospecção, por causa do PORTÃO: lá quem
    autoriza é o `_acesso` (sessão do painel, papel com 'vendas'); aqui é o
    `lead_do_vendedor`, que revalida a posse do lead. São dois mundos de permissão
    diferentes, e o vendedor no celular só existe neste.

    O id do lead entra no caminho de propósito: é ele que amarra a mensagem a uma
    conversa que o `lead_do_vendedor` já disse ser deste vendedor. Sem isso, o id da
    mensagem — que é sequencial e adivinhável — seria a única coisa entre um
    vendedor e a foto do cliente de outro.
    """
    sess = _sessao(request)
    if not sess:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    conta_id, membro_id = sess[0], sess[1]
    with get_pool().connection() as c:
        r = c.execute(
            """select m.midia_ref, m.midia_tipo, coalesce(m.midia_meta,'{}'::jsonb),
                      m.midia_arquivo
                 from mensagens m
                 join conversas cv on cv.id = m.conversa_id
                where m.id=%s and cv.conta_id=%s and cv.prospeccao_id=%s
                  and m.midia_ref is not null""",
            (mensagem_id, conta_id, lead_id)).fetchone()
    if not r:
        return Response(status_code=404)
    # ...e só agora a posse: a consulta acima diz que a mensagem é DESTE lead, o
    # lead_do_vendedor diz que o lead é DESTE vendedor.
    if not ck.lead_do_vendedor(get_pool(), conta_id, membro_id, lead_id):
        return Response(status_code=404)
    ref, tipo, meta, arquivo = r[0], r[1], (r[2] or {}), r[3]

    # O QUE FOI GUARDADO VEM DO NOSSO BUCKET (passo 5), e o CDN vira o plano B.
    # É a ordem que dá sentido ao botão: guardar tem que significar "não depende
    # mais do WhatsApp". Se continuássemos buscando no CDN primeiro, o arquivo
    # guardado só apareceria no dia em que o CDN já tivesse expirado — ou seja,
    # o caminho novo estrearia justamente quando ninguém pode testá-lo.
    if arquivo:
        from finance import midia_cofre as _mc
        try:
            dados, ct = _mc.ler(arquivo)
            nome_g = "".join(ch for ch in str(meta.get("nome") or "arquivo")
                             if ch.isprintable() and ch not in '"\\\r\n')[:120] or "arquivo"
            return Response(dados, media_type=ct, headers={
                "cache-control": "private, max-age=86400",
                "content-disposition": ('attachment; filename="%s"' % nome_g
                                        if tipo == "documento" else "inline")})
        except Exception as e:  # noqa: BLE001
            # cai pro CDN em vez de falhar: enquanto o WhatsApp ainda tiver o
            # arquivo, um problema no bucket não pode tirar a foto da tela
            import logging
            logging.getLogger("cockpit.midia").warning(
                "guardada %s ilegível (%s) — tentando o CDN", mensagem_id, e)

    from finance import wa_midia as _wm
    try:
        fluxo = _wm.buscar(ref, tipo)
        primeiro = next(fluxo, b"")
    except _wm.Expirou:
        return Response(status_code=410)
    except Exception as e:  # noqa: BLE001
        import logging
        logging.getLogger("cockpit.midia").warning(
            "midia %s falhou: %s: %s", mensagem_id, type(e).__name__, e)
        return Response(status_code=502)

    def _corpo():
        yield primeiro
        yield from fluxo

    nome = "".join(ch for ch in str(meta.get("nome") or "arquivo")
                   if ch.isprintable() and ch not in '"\\\r\n')[:120] or "arquivo"
    cab = {"cache-control": "private, max-age=86400",
           "content-disposition": ('attachment; filename="%s"' % nome
                                   if tipo == "documento" else "inline")}
    return StreamingResponse(_corpo(),
                             media_type=(ref.get("mimetype") or "").split(";")[0].strip()
                             or "application/octet-stream", headers=cab)


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
                  cidade: str = Form(""), uf: str = Form(""), obs: str = Form(""),
                  evento_tipo: str = Form(""), evento_em: str = Form(""),
                  evento_convidados: str = Form("")):
    """Ficha do cliente preenchida pelo vendedor, de dentro da conversa."""
    dados = {"empresa": empresa, "contato": contato, "cargo": cargo, "segmento": segmento,
             "telefone": telefone, "whatsapp": whatsapp, "documento": documento, "email": email,
             "cep": cep, "endereco": endereco, "numero": numero, "bairro": bairro,
             "nascimento": nascimento,
             "cidade": cidade, "uf": uf, "obs": obs,
             "evento_tipo": evento_tipo, "evento_em": evento_em,
             "evento_convidados": evento_convidados}
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


@router.get("/cockpit/novidades/{nid}", response_class=HTMLResponse)
def cockpit_novidade(request: Request, nid: int):
    """O aviso aberto, no app. Abrir marca a 'novidade' como lida (ganhar uma tela
    nova não precisa de confirmação); a 'mudanca' espera o "Entendi" — a mesma
    regra do painel (web/portal.painel_novidades). O que a tela mostra é o
    estado de ANTES de marcar, pra ele ainda ver o aviso como novo nesta visita."""
    sess = _sessao(request)
    if not sess:
        return RedirectResponse("/cockpit/login", status_code=303)
    conta_id, membro_id = sess
    n = next((x for x in _novidades_vend(conta_id, membro_id) if x["id"] == nid), None)
    if not n:
        return _page("Aviso", _hdr("Aviso", voltar=f"{_BASE}/perfil")
                     + "<div class=aviso>Esse aviso não é seu, ou não existe mais.</div>"
                     + _abas_vend("perfil", _pend_vend(conta_id, membro_id)))
    if n["tipo"] == "novidade" and not n["lida"]:
        try:
            from finance import novidades as nv
            nv.marcar_lida(get_pool(), n["id"], conta_id, membro_id)
        except Exception as e:  # noqa: BLE001
            _log.warning("marcar novidade %s lida: %s: %s", nid, type(e).__name__, e)
    tipo = "mudança" if n["tipo"] == "mudanca" else "novidade"
    botoes = ""
    if n["tipo"] == "mudanca" and not n["lida"]:
        botoes += (f"<form method=post action='{_BASE}/novidades/{n['id']}/lida' style='margin:0 0 .5rem'>"
                   "<input type=hidden name=volta value=perfil>"
                   "<button type=submit class=btn>Entendi</button></form>")
    if n.get("link"):
        botoes += (f"<a class='btn ghost' href='{esc(n['link'])}'>"
                   + ("Ver a Fila" if n["link"].rstrip("/") == _BASE else "Ver como ficou") + "</a>")
    corpo = (_hdr(n["titulo"], f"{tipo} · {n['publicado_em'].strftime('%d/%m')}", voltar=f"{_BASE}/perfil")
             + "<div class=scroll><div class=bloco>"
             + f"<div class=card><div class=nvcorpo>{esc(n['corpo'])}</div></div>"
             + f"<div style='margin-top:.6rem'>{botoes}</div>"
             + "</div></div>"
             + _abas_vend("perfil", _pend_vend(conta_id, membro_id)))
    return _page(n["titulo"], corpo)


@router.post("/cockpit/novidades/{nid}/lida")
def cockpit_novidade_lida(request: Request, nid: int, volta: str = Form("perfil")):
    """O "Entendi" da mudança, e o ✕ da faixa. Marca por PESSOA, e só o que este
    vendedor enxerga — id de aviso de outro público não vira linha em
    novidade_lida porque alguém postou o número."""
    sess = _sessao(request)
    if not sess:
        return RedirectResponse("/cockpit/login", status_code=303)
    conta_id, membro_id = sess
    if any(x["id"] == nid for x in _novidades_vend(conta_id, membro_id)):
        try:
            from finance import novidades as nv
            nv.marcar_lida(get_pool(), nid, conta_id, membro_id)
        except Exception as e:  # noqa: BLE001
            _log.warning("marcar novidade %s lida: %s: %s", nid, type(e).__name__, e)
    return RedirectResponse(_BASE if volta == "fila" else f"{_BASE}/perfil", status_code=303)


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
def cockpit_push_assinar(request: Request, sub: dict = Body(...)):
    sess = _sessao(request)
    if not sess:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    ok = ck.salvar_assinatura(get_pool(), sess[0], sess[1], sub)
    return JSONResponse({"ok": bool(ok)})


@router.post("/cockpit/push/remover")
def cockpit_push_remover(request: Request, sub: dict = Body(...)):
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
