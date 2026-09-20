"""O toque responde no PRIMEIRO toque (20/09/2026).

O vendedor tocava num botão, a tela ficava igual até a página nova chegar, e ele
tocava de novo — e o segundo toque CANCELAVA a navegação do primeiro, deixando o
app mais lento justamente pra quem já achava lento. Em form era pior: dois toques
em "Salvar" mandavam o form duas vezes.

Como no test_cockpit_enviar_sem_recarregar, o script roda de verdade contra um DOM
de mentira: o que importa é o que a tela FAZ em cada toque, não o texto do script.
"""
import json
import re
import shutil
import subprocess

import pytest

from web import painel_cockpit as pc

pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="sem node no ambiente")


def _script() -> str:
    # desde 20/09/2026 a constante é o JS puro: ele virou arquivo servido
    # (`_ESPERA_URL`) em vez de viajar dentro de cada navegação.
    return pc._ESPERA_JS


_HARNESS = r"""
globalThis.window = globalThis;
var log = {ouvintes: {}};

function El(tag, cls){
  this.tagName = tag; this.className = cls || ""; this.attrs = {}; this.parentNode = null;
  var self = this;
  this.classList = {
    add: function(c){ if(!self.tem(c)) self.className = (self.className + " " + c).trim(); },
    remove: function(c){ self.className = self.className.split(/\s+/)
                            .filter(function(x){ return x && x !== c; }).join(" "); },
    contains: function(c){ return self.tem(c); }
  };
}
El.prototype.tem = function(c){ return this.className.split(/\s+/).indexOf(c) >= 0; };
El.prototype.getAttribute = function(k){ return k in this.attrs ? this.attrs[k] : null; };
El.prototype.setAttribute = function(k, v){ this.attrs[k] = String(v); };
El.prototype.closest = function(sel){
  var n = this; while(n){ if(n.tagName === sel.toUpperCase()) return n; n = n.parentNode; }
  return null; };
El.prototype.querySelector = function(sel){ return this.botao || null; };
El.prototype.querySelectorAll = function(){ return []; };

var zprog = new El("SVG", "zprog");
globalThis.document = {
  getElementById: function(id){ return id === "zprog" ? zprog : null; },
  querySelectorAll: function(){ return []; },
  querySelector: function(){ return null; },
  addEventListener: function(t, fn){ (log.ouvintes[t] = log.ouvintes[t] || []).push(fn); }
};
window.addEventListener = function(){};
window.matchMedia = function(){ return {matches: false}; };
globalThis.location = {pathname: "/cockpit"};

function Evento(alvo){
  this.target = alvo; this.defaultPrevented = false;
  this.metaKey = false; this.ctrlKey = false;
  this.preventDefault = function(){ this.defaultPrevented = true; };
}
// dispara um clique/submit como o navegador faria: os ouvintes de CAPTURA do
// documento primeiro, e só depois quem estiver no elemento (que é quem chama
// preventDefault nos caminhos de fetch e do card deslizado).
function dispara(tipo, alvo, depoisDoCaptura){
  var ev = new Evento(alvo);
  (log.ouvintes[tipo] || []).forEach(function(fn){ fn(ev); });
  if(depoisDoCaptura) depoisDoCaptura(ev);
  return ev;
}
function link(href){ var a = new El("A"); a.setAttribute("href", href); return a; }
function formulario(){
  var f = new El("FORM"); f.botao = new El("BUTTON"); return f; }
function depois(fn){ setTimeout(fn, 0); }
"""


def _roda(cenario: str) -> dict:
    prog = _HARNESS + "\n" + _script() + "\n" + cenario
    r = subprocess.run(["node", "-e", prog], capture_output=True, text=True, timeout=20,
                       encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_o_primeiro_toque_ja_diz_que_esta_indo():
    """O Z começa a se desenhar no toque, não quando a tela nova chega."""
    out = _roda(r"""
      var a = link("/cockpit/agenda");
      var ev = dispara("click", a);
      console.log(JSON.stringify({prevenido: ev.defaultPrevented, z: zprog.className}));
    """)
    assert out["prevenido"] is False, "o primeiro toque navega como sempre"
    assert "on" in out["z"], "o Z liga no toque"


def test_segundo_toque_no_mesmo_link_e_ignorado():
    """Tocar de novo recomeçava a navegação do zero: o app ficava mais lento pra
    quem já achava lento."""
    out = _roda(r"""
      var a = link("/cockpit/agenda");
      dispara("click", a);
      depois(function(){
        var ev2 = dispara("click", a);
        console.log(JSON.stringify({segundo: ev2.defaultPrevented}));
      });
    """)
    assert out["segundo"] is True, "o toque repetido não pode cancelar a ida em curso"


def test_toque_que_nao_navegou_nao_prende_o_proximo():
    """O toque que só FECHA um card deslizado é prevenido por outro ouvinte e não
    leva a lugar nenhum. Se ele armasse a trava, o toque seguinte — o de verdade —
    morreria sem abrir o lead."""
    out = _roda(r"""
      var a = link("/cockpit/lead/7");
      dispara("click", a, function(ev){ ev.preventDefault(); });   // o card só fechou
      depois(function(){
        var ev2 = dispara("click", a);
        console.log(JSON.stringify({segundo: ev2.defaultPrevented}));
      });
    """)
    assert out["segundo"] is False, "o toque seguinte tem que abrir o lead"


def test_o_form_mostra_que_esta_enviando_e_nao_envia_duas_vezes():
    out = _roda(r"""
      var f = formulario();
      dispara("submit", f);
      depois(function(){
        var ev2 = dispara("submit", f);
        console.log(JSON.stringify({botao: f.botao.className, z: zprog.className,
                                    segundo: ev2.defaultPrevented}));
      });
    """)
    assert "ocupado" in out["botao"], "o botão precisa dizer que já está indo"
    assert "on" in out["z"]
    assert out["segundo"] is True, "dois toques em Salvar mandavam o form duas vezes"


def test_form_que_virou_fetch_nao_e_marcado():
    """O composer e a busca chamam `preventDefault()`: a tela NÃO vai trocar, então
    apagar o botão e ligar o Z seria mentir sobre uma navegação que não existe."""
    out = _roda(r"""
      var f = formulario();
      dispara("submit", f, function(ev){ ev.preventDefault(); });
      depois(function(){
        console.log(JSON.stringify({botao: f.botao.className, z: zprog.className}));
      });
    """)
    assert out["botao"] == "" and "on" not in out["z"]
