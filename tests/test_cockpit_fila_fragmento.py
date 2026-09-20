"""A fila se atualiza em PEDAÇOS, não recarregando a página (19/09/2026).

Antes, chegar mensagem numa conversa da lista disparava `location.reload()`: a
tela inteira de novo, ~1 s de branco, a rolagem voltando pro topo e o teclado
fechando — a cada mensagem. Agora vem só o topo, a lista e os selos das abas.

O script roda de verdade contra um DOM de mentira: o que importa é o que a tela
FAZ quando a assinatura muda, quando a resposta vem torta e quando o vendedor
está no meio de um gesto.
"""
import json
import re
import shutil
import subprocess

import pytest

from web import painel_cockpit as pc

pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="sem node no ambiente")


def _script() -> str:
    return re.findall(r"<script>(.*?)</script>", pc._sinal_js("v1"), re.S)[0]


_HARNESS = r"""
globalThis.window = globalThis;
var log = {urls: [], reload: 0, religou: 0, tique: null};

function El(){ this.innerHTML=""; this.textContent=""; this.scrollTop=0; }
var foco=new El(), lista=new El(), abas=new El(), sub=new El();
lista.scrollTop = 120;                      // o vendedor rolou a fila

var nos = {filafoco: foco, filalista: lista, filaabas: abas};
var aberto = null, focado = {tagName: "BODY"};
globalThis.document = {
  visibilityState: "visible",
  get activeElement(){ return focado; },
  getElementById: function(id){ return nos[id] || null; },
  querySelector: function(sel){
    if(sel === ".hdr .tt small") return sub;
    if(sel === ".front.open") return aberto;
    return null; },
  addEventListener: function(){}
};
globalThis.location = {search: "?entrou=tudo", reload: function(){ log.reload++; }};
globalThis.setInterval = function(fn){ log.tique = fn; };
window.CKBASE = "/cockpit";
window.__ligaFila = function(){ log.religou++; };

var sinal = {ok: true, sig: "v1"};
var fragmento = {ok: true, sig: "v2", sub: "3 abertos · 1 sua vez",
                 foco: "<div class=busca></div>", lista: "<div class=swipe>Lead</div>",
                 abas: "<div class=tabs></div>"};
globalThis.fetch = function(url){
  log.urls.push(url);
  var corpo = url.indexOf("/fila/sinal") >= 0 ? sinal : fragmento;
  return corpo === "rede" ? Promise.reject(new Error("offline"))
    : Promise.resolve({json: function(){ return Promise.resolve(corpo); }});
};
// o zapFetch entrega o corpo, e `null` quando a troca falhou
globalThis.zapFetch = function(url){
  log.urls.push(url);
  var corpo = url.indexOf("/fila/sinal") >= 0 ? sinal : fragmento;
  return Promise.resolve(corpo === "rede" ? null : corpo);
};
function depois(fn){ setTimeout(fn, 0); }
"""


def _roda(cenario: str) -> dict:
    prog = _HARNESS + "\n" + _script() + "\n" + cenario
    r = subprocess.run(["node", "-e", prog], capture_output=True, text=True, timeout=20,
                       encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_assinatura_igual_nao_busca_nada():
    """A fila parada não pede lista nenhuma — é o caso da esmagadora maioria dos
    tiques."""
    out = _roda(r"""
      log.tique();
      depois(function(){ depois(function(){
        console.log(JSON.stringify({urls: log.urls, reload: log.reload, lista: lista.innerHTML}));
      }); });
    """)
    assert out["urls"] == ["/cockpit/fila/sinal"]
    assert out["reload"] == 0 and out["lista"] == ""


def test_mudou_troca_os_pedacos_e_guarda_a_rolagem():
    out = _roda(r"""
      sinal = {ok: true, sig: "v2"};
      log.tique();
      depois(function(){ depois(function(){ depois(function(){
        console.log(JSON.stringify({urls: log.urls, reload: log.reload, religou: log.religou,
          foco: foco.innerHTML, lista: lista.innerHTML, abas: abas.innerHTML,
          sub: sub.textContent, scroll: lista.scrollTop}));
      }); }); });
    """)
    assert out["urls"] == ["/cockpit/fila/sinal", "/cockpit/fila/fragmento?entrou=tudo"], \
        "o fragmento respeita o mês/busca/ordem que estão na barra de endereço"
    assert out["reload"] == 0, "nada de recarregar a tela"
    assert out["lista"] == "<div class=swipe>Lead</div>"
    assert out["foco"] == "<div class=busca></div>" and out["abas"] == "<div class=tabs></div>"
    assert out["sub"] == "3 abertos · 1 sua vez"
    assert out["scroll"] == 120, "a rolagem fica onde o vendedor deixou"
    assert out["religou"] == 1, "os cards novos precisam do deslize religado"


def test_resposta_torta_recarrega_em_vez_de_deixar_tela_velha():
    out = _roda(r"""
      sinal = {ok: true, sig: "v2"};
      fragmento = {ok: false};
      log.tique();
      depois(function(){ depois(function(){ depois(function(){
        console.log(JSON.stringify({reload: log.reload, lista: lista.innerHTML}));
      }); }); });
    """)
    assert out["reload"] == 1 and out["lista"] == ""


def test_nao_atropela_quem_esta_no_meio_de_um_gesto():
    """Card aberto no deslize, campo em foco ou app em segundo plano: o tique passa
    e tenta de novo no próximo — trocar a lista debaixo do dedo é pior que esperar."""
    out = _roda(r"""
      sinal = {ok: true, sig: "v2"};
      aberto = {};                      // card aberto no deslize
      log.tique();
      focado = {tagName: "INPUT"}; aberto = null;
      log.tique();
      document.visibilityState = "hidden"; focado = {tagName: "BODY"};
      log.tique();
      depois(function(){ console.log(JSON.stringify({urls: log.urls})); });
    """)
    assert out["urls"] == [], "nenhuma das três situações pode buscar"


def test_campo_focado_no_meio_do_voo_nao_perde_o_que_foi_digitado():
    """A guarda do gesto valia SÓ no começo do tique — e entre ele e a troca há duas
    idas ao servidor. Quem tocasse na busca nesse intervalo via o `innerHTML` chegar
    por cima: o texto sumia e o teclado fechava no meio da palavra (20/09/2026).

    Aqui o tique começa com o BODY em foco, como sempre, e o vendedor toca no campo
    enquanto a resposta está no ar. A tela não pode ser trocada."""
    out = _roda(r"""
      sinal = {ok: true, sig: "v2"};
      log.tique();                       // começa liberado: nada em foco
      focado = {tagName: "INPUT"};       // o vendedor tocou na busca no meio do voo
      depois(function(){ depois(function(){ depois(function(){
        console.log(JSON.stringify({lista: lista.innerHTML, foco: foco.innerHTML,
                                    reload: log.reload}));
      }); }); });
    """)
    assert out["lista"] == "" and out["foco"] == "", "nada foi trocado por cima de quem digita"
    assert out["reload"] == 0, "e muito menos recarregar a tela"
