"""A busca da Fila procura AO DIGITAR, não só no Enter (20/09/2026).

O defeito que este teste tranca: o `zapFetch` mora num arquivo carregado com
`defer`, e script com `defer` roda DEPOIS de todo script inline — inclusive o da
busca. Conferir `window.zapFetch` na montagem desligava a busca ao digitar por
completo, e sobrava só o Enter do formulário. O vendedor digitava o nome e a fila
inteira continuava na tela.

Quem digita é gente, e gente digita depois da tela pronta: a checagem certa é na
hora de buscar, não na hora de montar.
"""
import json
import re
import shutil
import subprocess

import pytest

from web import painel_cockpit as pc

pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="sem node no ambiente")


def _script() -> str:
    return re.findall(r"<script>(.*?)</script>", pc._busca_js(), re.S)[0]


_HARNESS = r"""
globalThis.window = globalThis;
var log = {urls: [], submits: 0};

function El(tag, cls){ this.tagName = tag; this.className = cls || ""; this.attrs = {};
  this.ouvintes = {}; this.value = ""; this.filhos = []; }
El.prototype.addEventListener = function(t, fn){ (this.ouvintes[t] = this.ouvintes[t] || []).push(fn); };
El.prototype.dispara = function(t, ev){ (this.ouvintes[t] || []).forEach(function(fn){ fn(ev || {preventDefault: function(){}}); }); };
El.prototype.setAttribute = function(k, v){ this.attrs[k] = v; };
El.prototype.getAttribute = function(k){ return k in this.attrs ? this.attrs[k] : null; };
El.prototype.appendChild = function(c){ this.filhos.push(c); return c; };
El.prototype.removeChild = function(){};
El.prototype.blur = function(){};
El.prototype.submit = function(){ log.submits++; };
El.prototype.querySelector = function(sel){
  if(sel === "form") return this.form_ || null;
  if(sel === "input[name=q]") return this.campo_ || null;
  if(sel === ".lm") return this.lm_ || null;
  return null; };

var campo = new El("INPUT");
var form = new El("FORM", "busca"); form.campo_ = campo;
var caixa = new El("DIV"); caixa.form_ = form; caixa.campo_ = campo;
form.firstChild = new El("SPAN"); form.firstChild.nextSibling = campo;

globalThis.document = {
  getElementById: function(id){ return id === "filabusca" ? caixa : null; },
  querySelector: function(){ return null; },
  createElement: function(t){ return new El(t.toUpperCase()); },
  addEventListener: function(){}
};
globalThis.location = {pathname: "/cockpit", search: ""};
globalThis.history = {replaceState: function(){}};
window.CKBASE = "/cockpit";
// PONTO DO TESTE: na montagem o zapFetch AINDA NAO EXISTE (o arquivo dele tem
// `defer` e roda depois deste script). Ele aparece so mais tarde.
delete globalThis.zapFetch;

function digita(txt){ campo.value = txt; campo.dispara("input"); }
function liga_zapfetch(){
  globalThis.zapFetch = function(url){
    log.urls.push(url);
    return Promise.resolve({ok: true, foco: "", lista: "", abas: "", sub: "1 de 4 · busca"});
  };
}
function depois(fn){ setTimeout(fn, 400); }   // passa do debounce de 300ms
"""


def _roda(cenario: str) -> dict:
    prog = _HARNESS + "\n" + _script() + "\n" + cenario
    r = subprocess.run(["node", "-e", prog], capture_output=True, text=True, timeout=20,
                       encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_duas_letras_ja_procuram_mesmo_com_o_zapfetch_chegando_depois():
    """O defeito de hoje: a busca desistia na montagem porque o zapFetch (arquivo
    com `defer`) ainda não tinha carregado."""
    out = _roda(r"""
      liga_zapfetch();
      digita("Ke");
      depois(function(){ console.log(JSON.stringify({urls: log.urls, submits: log.submits})); });
    """)
    assert out["urls"] == ["/cockpit/fila/fragmento?q=Ke"], "duas letras têm que buscar"
    assert out["submits"] == 0, "sem recarregar a tela"


def test_uma_letra_nao_e_busca():
    """"a" traria a fila inteira de volta e custaria uma viagem ao banco por
    tecla."""
    out = _roda(r"""
      liga_zapfetch();
      digita("K");
      depois(function(){ console.log(JSON.stringify({urls: log.urls})); });
    """)
    assert out["urls"] == []


def test_limpar_devolve_a_fila():
    """O campo vazio sempre vale: é como o vendedor desfaz a busca."""
    out = _roda(r"""
      liga_zapfetch();
      digita("Kelly");
      setTimeout(function(){
        digita("");
        depois(function(){ console.log(JSON.stringify({urls: log.urls})); });
      }, 400);
    """)
    assert out["urls"] == ["/cockpit/fila/fragmento?q=Kelly", "/cockpit/fila/fragmento"]


def test_sem_zapfetch_de_jeito_nenhum_o_form_ainda_manda():
    """Navegador onde o arquivo não carregou: a busca vira o GET de sempre, em vez
    de não acontecer."""
    out = _roda(r"""
      digita("Kelly");
      depois(function(){ console.log(JSON.stringify({submits: log.submits, urls: log.urls})); });
    """)
    assert out["submits"] == 1 and out["urls"] == []
