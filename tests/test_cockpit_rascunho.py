"""O rascunho da conversa: o que foi escrito e não enviado não se perde.

POR QUE EXISTE. O vendedor escreve metade da resposta, sai pra conferir a agenda
ou a ficha do lead, volta — e o texto tinha sumido. Quem redigita uma vez, na
segunda responde pelo WhatsApp do celular, que é o hábito que este app existe pra
fechar.

ONDE FICA. No aparelho (localStorage), por conversa, e NUNCA no servidor: é
texto não enviado, rascunho de quem escreveu. Some quando ele cai na tela de
entrada — pelo Sair ou pela sessão expirada —, pelo mesmo motivo que o service
worker deixou de guardar conversa: aparelho de vendedor é trocado e emprestado.

O script roda de verdade num DOM de mentira: o que importa é o que a tela FAZ ao
abrir, ao digitar, ao enviar e quando o envio falha.
"""
import json

import shutil
import subprocess

import pytest

from web import painel_cockpit as pc

pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="sem node no ambiente")


def _script() -> str:
    """O `_ESPERA_JS` virou ARQUIVO servido (espera.js) em 20/09/2026 — eram 7,6 KB
    repetidos em cada navegação. Agora ele já é JavaScript puro, sem as tags."""
    return pc._ESPERA_JS


_HARNESS = r"""
globalThis.window = globalThis;
var log = {fetch: [], puxa: 0, nav: null};
var guardado = {};                         // o "aparelho"

globalThis.localStorage = {
  _d: guardado,
  getItem: function(k){ return Object.prototype.hasOwnProperty.call(guardado,k) ? guardado[k] : null; },
  setItem: function(k,v){ guardado[k] = String(v); },
  removeItem: function(k){ delete guardado[k]; },
  key: function(i){ return Object.keys(guardado)[i]; },
  get length(){ return Object.keys(guardado).length; }
};

function El(tag, cls){
  this.tagName = tag; this.className = cls || ""; this.children = []; this.parentNode = null;
  this.attrs = {}; this.style = {}; this.textContent = ""; this.value = ""; this.disabled = false;
  this.ouvintes = {};
}
El.prototype.setAttribute = function(k,v){ this.attrs[k] = String(v); };
El.prototype.getAttribute = function(k){ return k in this.attrs ? this.attrs[k] : null; };
El.prototype.removeAttribute = function(k){ delete this.attrs[k]; };
El.prototype.appendChild = function(c){ c.parentNode = this; this.children.push(c); return c; };
El.prototype.insertBefore = function(c, ref){ c.parentNode = this;
  var i = this.children.indexOf(ref); this.children.splice(i<0?0:i, 0, c); return c; };
El.prototype.remove = function(){ if(!this.parentNode) return;
  var p = this.parentNode.children; p.splice(p.indexOf(this),1); this.parentNode = null; };
El.prototype.addEventListener = function(t, fn){ (this.ouvintes[t] = this.ouvintes[t]||[]).push(fn); };
El.prototype.dispara = function(t){ (this.ouvintes[t]||[]).forEach(function(fn){ fn({}); }); };
El.prototype.blur = function(){}; El.prototype.focus = function(){};
El.prototype.todos = function(){ var out=[]; this.children.forEach(function(c){
  out.push(c); out = out.concat(c.todos()); }); return out; };
El.prototype.querySelector = function(sel){ return this.querySelectorAll(sel)[0] || null; };
El.prototype.querySelectorAll = function(sel){
  return this.todos().filter(function(e){ return casa(e, sel); }); };
function casa(e, sel){
  if(sel === "[name=texto]" || sel === "textarea[name=texto]")
    return e.getAttribute("name") === "texto" && (sel[0] === "[" || e.tagName === "TEXTAREA");
  if(sel === "button[type=submit]") return e.tagName === "BUTTON";
  if(sel === ".tick") return e.className === "tick";
  if(sel === ".travabl") return e.className === "travabl";
  if(sel === ".envio-err") return e.className === "envio-err";
  if(sel === ".bub.voando[data-txt]") return /voando/.test(e.className) && e.getAttribute("data-txt") !== null;
  return false;
}

var rodape = new El("DIV", "rodape");
var form = new El("FORM", "composer");
form.action = "https://zaq/cockpit/lead/812/mensagem";
var campo = new El("TEXTAREA"); campo.setAttribute("name", "texto");
var botao = new El("BUTTON");
form.appendChild(campo); form.appendChild(botao); rodape.appendChild(form);
var chat = new El("DIV", "chat"); chat.scrollHeight = 500;

globalThis.document = {
  getElementById: function(){ return null; },
  addEventListener: function(){},
  querySelector: function(sel){
    if(sel === "form.composer") return form;
    if(sel === ".chat") return chat;
    return null; },
  createElement: function(tag){ return new El(tag.toUpperCase()); }
};
window.addEventListener = function(){};
window.matchMedia = function(){ return {matches:false}; };
globalThis.location = {pathname: "/cockpit/lead/812", set href(v){ log.nav = v; }};
globalThis.FormData = function(){ return [["texto", campo.value]]; };
window.__puxa = function(){ log.puxa++; };

var resposta = {ok: true};
globalThis.zapFetch = function(url, opt){
  log.fetch.push({url: url, corpo: String(opt.body)});
  return Promise.resolve(resposta);
};

function digitar(txt){ campo.value = txt; campo.dispara("input"); }
function enviar(){
  var ev = {prevenido:false, preventDefault:function(){ this.prevenido = true; }};
  form.ouvintes.submit.forEach(function(fn){ fn(ev); });
  return ev;
}
function depois(fn){ setTimeout(fn, 0); }
"""


def _roda(cenario: str, antes: str = "") -> dict:
    prog = _HARNESS + "\n" + antes + "\n" + _script() + "\n" + cenario
    r = subprocess.run(["node", "-e", prog], capture_output=True, text=True, timeout=20,
                       encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_o_que_foi_digitado_fica_guardado_por_conversa():
    out = _roda(r"""
      digitar("Oi Juliana, sobre o sábado");
      console.log(JSON.stringify({guardado: guardado}));
    """)
    assert out["guardado"] == {"ck_rasc_812": "Oi Juliana, sobre o sábado"}, \
        "a chave leva o id do lead: rascunho é POR conversa"


def test_ao_abrir_a_conversa_o_rascunho_volta_pra_caixa():
    out = _roda(r"""
      console.log(JSON.stringify({campo: campo.value}));
    """, antes='guardado["ck_rasc_812"] = "faltou mandar isto";')
    assert out["campo"] == "faltou mandar isto"


def test_texto_vindo_do_servidor_ganha_do_rascunho():
    """A pergunta pronta da Fila (`?texto=`) e o texto que não saiu vêm na caixa
    pelo servidor. O guardado no aparelho não pode passar por cima deles."""
    out = _roda(r"""
      console.log(JSON.stringify({campo: campo.value}));
    """, antes='guardado["ck_rasc_812"] = "rascunho velho"; campo.value = "Pra qual data?";')
    assert out["campo"] == "Pra qual data?"


def test_enviar_apaga_o_rascunho():
    out = _roda(r"""
      digitar("vai sair agora");
      enviar();
      depois(function(){ depois(function(){
        console.log(JSON.stringify({guardado: guardado, campo: campo.value, fetch: log.fetch.length}));
      }); });
    """)
    assert out["fetch"] == 1 and out["campo"] == ""
    assert out["guardado"] == {}, "mensagem que saiu não é mais rascunho"


def test_envio_que_falha_volta_a_ser_rascunho():
    """O texto volta pra caixa E volta a ficar guardado: se ele sair da tela pra
    ver o que houve, não pode perder o que escreveu."""
    out = _roda(r"""
      resposta = {ok: false, erro: "O WhatsApp da empresa desconectou.", codigo: "x"};
      digitar("mensagem que não saiu");
      enviar();
      depois(function(){ depois(function(){
        console.log(JSON.stringify({guardado: guardado, campo: campo.value}));
      }); });
    """)
    assert out["campo"] == "mensagem que não saiu"
    assert out["guardado"] == {"ck_rasc_812": "mensagem que não saiu"}


def test_apagar_tudo_na_caixa_apaga_o_rascunho():
    out = _roda(r"""
      digitar("escrevi");
      digitar("");
      console.log(JSON.stringify({guardado: guardado}));
    """)
    assert out["guardado"] == {}


def test_aparelho_sem_localStorage_nao_derruba_a_conversa():
    """Navegador em janela anônima, armazenamento bloqueado: a conversa continua."""
    out = _roda(r"""
      digitar("segue funcionando");
      var ev = enviar();
      depois(function(){ depois(function(){
        console.log(JSON.stringify({prevenido: ev.prevenido, fetch: log.fetch.length}));
      }); });
    """, antes="""
      globalThis.localStorage = {
        getItem: function(){ throw new Error("bloqueado"); },
        setItem: function(){ throw new Error("bloqueado"); },
        removeItem: function(){ throw new Error("bloqueado"); },
        key: function(){ throw new Error("bloqueado"); },
        get length(){ throw new Error("bloqueado"); }
      };
    """)
    assert out["prevenido"] is True and out["fetch"] == 1


def test_a_tela_de_entrada_apaga_os_rascunhos():
    """Pelo Sair ou pela sessão expirada, quem chega na entrada deixa o aparelho
    limpo — aparelho de vendedor é trocado, emprestado e perdido."""
    js = pc._LIMPA_RASCUNHOS_JS
    assert "ck_rasc_" in js and "localStorage.removeItem" in js
    html = bytes(pc._tela_login("x", "y").body).decode("utf-8")
    assert "ck_rasc_" in html, "a limpeza tem que estar NA tela de entrada"
