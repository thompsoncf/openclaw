"""Enviar mensagem no app sem recarregar a tela (19/09/2026).

Até aqui enviar era POST + redirect + a conversa inteira de novo: ~1 s de tela
piscando por mensagem e o teclado fechando — o vendedor comparava com o WhatsApp,
onde nada disso acontece. Agora o texto vai por fetch e a bolha otimista vira ✓.

`node --check` não basta aqui: o que importa é o que a tela FAZ em cada resposta
(deu certo, falhou, a trava engatou). Então o script roda de verdade, contra um
DOM de mentira com só o que ele toca — o mesmo jeito do test_cockpit_voz_tela.
"""
import json
import re
import shutil
import subprocess

import pytest

from web import painel_cockpit as pc

pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="sem node no ambiente")


def _script() -> str:
    return re.findall(r"<script>(.*?)</script>", pc._ESPERA_JS, re.S)[0]


_HARNESS = r"""
globalThis.window = globalThis;
var log = {fetch: [], puxa: 0, nav: null};

function El(tag, cls){
  this.tagName = tag; this.className = cls || ""; this.children = []; this.parentNode = null;
  this.attrs = {}; this.style = {}; this.textContent = ""; this.value = ""; this.disabled = false;
  this.ouvintes = {};
}
El.prototype.setAttribute = function(k, v){ this.attrs[k] = String(v); };
El.prototype.getAttribute = function(k){ return k in this.attrs ? this.attrs[k] : null; };
El.prototype.removeAttribute = function(k){ delete this.attrs[k]; };
El.prototype.appendChild = function(c){ c.parentNode = this; this.children.push(c); return c; };
El.prototype.insertBefore = function(c, ref){
  c.parentNode = this; var i = this.children.indexOf(ref);
  this.children.splice(i < 0 ? 0 : i, 0, c); return c; };
El.prototype.remove = function(){
  if(!this.parentNode) return; var p = this.parentNode.children;
  p.splice(p.indexOf(this), 1); this.parentNode = null; };
El.prototype.addEventListener = function(t, fn){ (this.ouvintes[t] = this.ouvintes[t] || []).push(fn); };
El.prototype.blur = function(){}; El.prototype.focus = function(){};
El.prototype.todos = function(){
  var out = []; this.children.forEach(function(c){ out.push(c); out = out.concat(c.todos()); });
  return out; };
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
var form = new El("FORM", "composer"); form.action = "/cockpit/lead/7/mensagem";
var campo = new El("TEXTAREA"); campo.setAttribute("name", "texto");
var botao = new El("BUTTON"); form.appendChild(campo); form.appendChild(botao);
rodape.appendChild(form);
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
window.matchMedia = function(){ return {matches: false}; };
globalThis.location = {pathname: "/cockpit/lead/7", set href(v){ log.nav = v; }};
globalThis.FormData = function(f){ return [["texto", campo.value]]; };
window.__puxa = function(){ log.puxa++; };

var resposta = null;
globalThis.fetch = function(url, opt){
  log.fetch.push({url: url, headers: opt.headers, corpo: String(opt.body)});
  return resposta === "rede"
    ? Promise.reject(new Error("offline"))
    : Promise.resolve({json: function(){ return Promise.resolve(resposta); }});
};

function envia(txt){
  campo.value = txt;
  var ev = {prevenido: false, preventDefault: function(){ this.prevenido = true; }};
  form.ouvintes.submit.forEach(function(fn){ fn(ev); });
  return ev;
}
function depois(fn){ setTimeout(fn, 0); }
"""


def _roda(cenario: str) -> dict:
    prog = _HARNESS + "\n" + _script() + "\n" + cenario
    r = subprocess.run(["node", "-e", prog], capture_output=True, text=True, timeout=20,
                       encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_deu_certo_nao_recarrega_e_a_bolha_vira_check():
    out = _roda(r"""
      resposta = {ok: true};
      var ev = envia("  Bom dia! O pacote é R$ 5 mil  ");
      depois(function(){ depois(function(){
        var b = chat.children[0];
        console.log(JSON.stringify({prevenido: ev.prevenido, fetch: log.fetch, puxa: log.puxa,
          nav: log.nav, campo: campo.value, n: chat.children.length,
          txt: b.getAttribute("data-txt"), tick: b.children[0].textContent}));
      }); });
    """)
    assert out["prevenido"] is True, "o form não pode seguir pro POST + redirect"
    assert out["nav"] is None
    assert len(out["fetch"]) == 1
    f = out["fetch"][0]
    assert f["url"] == "/cockpit/lead/7/mensagem"
    assert f["headers"]["x-cockpit"] == "1"
    assert "texto=" in f["corpo"] and "pacote" in f["corpo"]
    assert out["campo"] == "", "o campo esvazia na hora, como no WhatsApp"
    assert out["n"] == 1 and out["txt"] == "Bom dia! O pacote é R$ 5 mil"
    assert out["tick"] == "✓"
    assert out["puxa"] == 1, "a mensagem de verdade vem pelo polling, puxado na hora"


def test_falhou_o_texto_volta_pra_caixa_e_diz_por_que():
    out = _roda(r"""
      resposta = {ok: false, erro: "O WhatsApp da empresa desconectou.", codigo: "x"};
      envia("Oi Sheila");
      depois(function(){ depois(function(){
        var e = rodape.children[0];
        console.log(JSON.stringify({campo: campo.value, bolhas: chat.children.length,
          cls: e.className, msg: e.textContent, nav: log.nav}));
      }); });
    """)
    assert out["campo"] == "Oi Sheila", "redigitar é o que manda o vendedor pro WhatsApp"
    assert out["bolhas"] == 0, "a bolha de quem não saiu some"
    assert out["cls"] == "envio-err" and "desconectou" in out["msg"]
    assert out["nav"] is None


def test_sem_rede_tambem_devolve_o_texto():
    out = _roda(r"""
      resposta = "rede";
      envia("Oi");
      depois(function(){ depois(function(){
        console.log(JSON.stringify({campo: campo.value, msg: rodape.children[0].textContent}));
      }); });
    """)
    assert out["campo"] == "Oi"
    assert "conexão" in out["msg"]


def test_trava_que_engatou_recarrega_com_o_texto_na_caixa():
    """A trava pede os rádios do motivo, que só o servidor desenha."""
    out = _roda(r"""
      resposta = {ok: false, erro: "Escolha por que insistir", codigo: "trava_justifique"};
      envia("Oi de novo");
      depois(function(){ depois(function(){
        console.log(JSON.stringify({nav: log.nav}));
      }); });
    """)
    assert out["nav"] == "/cockpit/lead/7?texto=Oi%20de%20novo"


def test_com_a_trava_na_tela_segue_o_form_de_sempre():
    """Rádios obrigatórios e a volta com o motivo são do fluxo do form."""
    out = _roda(r"""
      form.appendChild(new El("DIV", "travabl"));
      var ev = envia("Oi");
      console.log(JSON.stringify({prevenido: ev.prevenido, fetch: log.fetch.length,
        hidden: form.children.filter(function(c){ return c.name === "texto"; }).length}));
    """)
    assert out["prevenido"] is False and out["fetch"] == 0
    assert out["hidden"] == 1, "o texto migra pro hidden antes de esvaziar o campo"


def test_vazio_nao_envia():
    out = _roda(r"""
      var ev = envia("   ");
      console.log(JSON.stringify({prevenido: ev.prevenido, fetch: log.fetch.length,
        bolhas: chat.children.length}));
    """)
    assert out == {"prevenido": True, "fetch": 0, "bolhas": 0}


def _tela_da_conversa(texto_pre: str = "") -> str:
    """A tela do lead renderizada de verdade, sem banco: `d` é o que o
    `lead_do_vendedor` devolveria pra um lead simples com duas mensagens."""
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from starlette.datastructures import QueryParams
    agora = datetime.now(timezone.utc)
    d = {"empresa": "Sheila", "cidade": "Teresina", "uf": "PI", "doc_fmt": "",
         "evento_fmt": "", "status": "novo", "ia": False, "trava": None, "zap_link": "",
         "etapas": [{"chave": "novo", "rotulo": "Novo"}], "motivos_perda": [],
         "aviso_conversa": {}, "evento_pista": None,
         "mensagens": [{"id": 10, "who": "in", "texto": "Oi, tem data?", "quando": agora},
                       {"id": 11, "who": "out", "texto": "Tem sim!", "quando": agora}]}
    req = SimpleNamespace(session={}, query_params=QueryParams(
        f"texto={texto_pre}" if texto_pre else ""))
    # pode_voz: com o microfone e o clipe na tela, que é o caso da conta QR — é
    # assim que o JS do anexo entra no render e é compilado junto
    return bytes(pc._lead_vendedor(req, 7, d, pode_voz=True).body).decode("utf-8")


def test_todo_script_da_tela_da_conversa_compila(tmp_path):
    """A tela junta strings Python comuns (sem `r`), onde um escape errado vira
    SyntaxError só DEPOIS do render — e derruba o bloco inteiro, envio incluído."""
    html = _tela_da_conversa()
    scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
    assert len(scripts) >= 3
    for i, s in enumerate(scripts):
        arq = tmp_path / f"s{i}.js"
        arq.write_text(s, encoding="utf-8")
        r = subprocess.run(["node", "--check", str(arq)], capture_output=True, text=True,
                           encoding="utf-8")
        assert r.returncode == 0, f"script {i} não compila:\n{r.stderr}\n{s[:400]}"


def test_a_caixa_de_resposta_e_textarea_e_devolve_o_texto():
    html = _tela_da_conversa("Pra qual data?")
    assert "<textarea name=texto rows=1" in html
    assert " autofocus>Pra qual data?</textarea>" in html
    assert "<input name=texto" not in html


def test_foto_enviada_nao_aparece_duas_vezes():
    """19/09/2026, logo depois do deploy: o dono mandou uma foto de teste e ela
    apareceu DUAS vezes. No banco era uma só (id 165561). A corrida: a mensagem é
    gravada antes da resposta do upload voltar (o servidor espera o WhatsApp
    receber o arquivo), e o polling que passa nesse meio tempo a traz de novo.

    As duas ordens de chegada têm guarda:
      * polling primeiro → quando o upload responde, a bolha LOCAL sai;
      * upload primeiro  → a bolha local ganha o id, e o polling pula quem já tem.
    """
    html = _tela_da_conversa()
    assert "ja && ja!==d" in html and "d.remove(); return;" in html
    assert """if(chat.querySelector('.bub[data-id="'+m.id+'"]'))""" in html


def test_a_bolha_de_verdade_toma_o_lugar_da_otimista():
    """O polling traz a mensagem gravada; a otimista, com o mesmo texto, sai."""
    fonte = __import__("inspect").getsource(pc._lead_vendedor)
    assert ".bub.voando[data-txt]" in fonte and "vs[k].remove()" in fonte
    assert "getAttribute('data-txt')===m.texto" in fonte
