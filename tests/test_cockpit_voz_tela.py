"""O gravador de voz RESPONDENDO AO TOQUE — a reclamação que gerou este arquivo.

"os botões não estão instantâneos, a primeiro momento eu pensei que travou."

E era verdade. A primeira versão tinha três momentos mortos, e todos pela mesma
causa: a tela só mudava DEPOIS que um trabalho assíncrono terminava.

  1. tocar no microfone → a barra só aparecia quando o `getUserMedia` resolvia,
     que no celular leva de 0,3 a 2s (permissão + hardware);
  2. tocar em enviar → o "enviando…" só aparecia depois do `mr.stop()` disparar o
     `onstop`, e aí ainda decodificava o ÁUDIO INTEIRO pra calcular a onda —
     quanto mais longo o áudio, mais tela parada;
  3. depois de enviar → `location.reload()`, ~1s de página em branco.

O que estes testes prendem é a PROPRIEDADE, não o desenho: o estado da tela muda
no mesmo instante do toque, antes de qualquer promessa resolver. `node --check`
não pega isso — só executando dá pra saber QUANDO a tela mudou.

O gravador roda de verdade, contra um DOM de mentira e um `getUserMedia` que eu
seguro de propósito.
"""
import json
import re
import shutil
import subprocess

import pytest

from web import painel_cockpit as pc


def _script() -> str:
    """O script como ele SAI pra tela — com os marcadores já trocados, igual o
    `_lead_vendedor` faz. Testar o template cru rodaria outro código."""
    js = pc._VOZ_JS.replace("__BASE__", pc._BASE).replace("__LEAD__", "7")
    return re.findall(r"<script>(.*?)</script>", js, re.S)[0]


_HARNESS = r"""
globalThis.window = globalThis;

// ---- DOM de mentira: só o que o gravador toca ----
function No(id){
  this.id=id; this.style={}; this.textContent=""; this.disabled=false; this.onclick=null;
  var c={};
  this.classList={
    add:function(){for(var i=0;i<arguments.length;i++)c[arguments[i]]=1;},
    remove:function(){for(var i=0;i<arguments.length;i++)delete c[arguments[i]];},
    contains:function(x){return !!c[x];},
    _todas:function(){return Object.keys(c);}
  };
}
var nos={};
globalThis.document={getElementById:function(id){return nos[id]||(nos[id]=new No(id));}};
function el(id){return document.getElementById(id);}

// ---- relógio e animação sob controle ----
var agora=1000000;
Date.now=function(){return agora;};
var quadros=[];
globalThis.requestAnimationFrame=function(fn){quadros.push(fn);return quadros.length;};
globalThis.cancelAnimationFrame=function(){};
globalThis.setInterval=function(){return 1;};
globalThis.clearInterval=function(){};
globalThis.setTimeout=function(fn){return fn && fn();};
globalThis.alert=function(m){globalThis.__alerta=m;};
globalThis.btoa=function(s){return Buffer.from(s,'binary').toString('base64');};
globalThis.location={reload:function(){globalThis.__recarregou=true;}};

// ---- o microfone: a promessa fica PRESA até o teste soltar ----
var soltaFluxo, fluxoFalso={getTracks:function(){return [{stop:function(){}}];}};
Object.defineProperty(globalThis,"navigator",{configurable:true,writable:true,
  value:{mediaDevices:{getUserMedia:function(){
    globalThis.__pediuMic=true;
    return new Promise(function(ok){ soltaFluxo=function(){ ok(fluxoFalso); }; });
  }}}});

// ---- MediaRecorder de mentira ----
function MediaRecorder(st, opts){
  this.state="inactive"; this.mimeType=(opts&&opts.mimeType)||"";
  globalThis.__taxa=(opts&&opts.audioBitsPerSecond)||null;
  var eu=this;
  this.start=function(){ eu.state="recording"; };
  // stop() NÃO dispara onstop na hora — é assim no navegador, e era justamente
  // essa folga que deixava o botão de enviar parecendo morto
  this.stop=function(){ eu.state="inactive"; globalThis.__pediuStop=true; };
  this.dispararStop=function(){
    if(eu.ondataavailable) eu.ondataavailable({data:{size:10}});
    if(eu.onstop) eu.onstop();
  };
  globalThis.__mr=this;
}
MediaRecorder.isTypeSupported=function(t){ return t===globalThis.__tipoAceito; };
globalThis.MediaRecorder=MediaRecorder;

// ---- Web Audio: se decodeAudioData for chamado, o teste vê ----
globalThis.__decodeChamado=0;
function AudioContext(){
  this.state="suspended";
  this.resume=function(){ this.state="running"; };
  this.close=function(){ globalThis.__ctxFechado=true; };
  this.createAnalyser=function(){
    return {fftSize:512, getByteTimeDomainData:function(b){
      for(var i=0;i<b.length;i++) b[i]=128+globalThis.__amplitude;
    }, connect:function(){}};
  };
  this.createMediaStreamSource=function(){ return {connect:function(){}}; };
  this.decodeAudioData=function(){ globalThis.__decodeChamado++; };
}
globalThis.AudioContext=AudioContext;
globalThis.__amplitude=40;

globalThis.Blob=function(partes,o){
  this.type=(o&&o.type)||"";
  this.arrayBuffer=function(){ return Promise.resolve(new Uint8Array([1,2,3]).buffer); };
};
var envios=[];
globalThis.fetch=function(url,o){
  envios.push({url:url, headers:(o&&o.headers)||{}});
  return Promise.resolve({json:function(){ return Promise.resolve(globalThis.__resposta); }});
};
globalThis.__resposta={ok:true};
globalThis.__puxa=function(){ globalThis.__puxou=true; };
globalThis.__tipoAceito="audio/webm;codecs=opus";
globalThis.__envios=envios;

__SCRIPT__

// ═══════════════════════════════════════════════════════════════
var erros=[], ok=function(c,m){ if(!c) erros.push(m); };
var st=function(){ return el("grav").classList._todas().filter(function(x){
  return x.indexOf("st-")===0; }).join(",") || "(nenhum)"; };
var flush=function(){ return new Promise(function(r){ process.nextTick(r); }); };

(async function(){
  // ── 1. o TOQUE no microfone muda a tela na hora ──────────────
  el("mic").onclick();
  ok(el("grav").classList.contains("on"), "a barra não abriu no toque");
  ok(st()==="st-prep", "estado no toque devia ser st-prep, veio " + st());
  ok(el("dica").textContent==="preparando…", "sem aviso de preparando: " + el("dica").textContent);
  ok(globalThis.__pediuMic, "nem pediu o microfone");
  // e isso TUDO antes de o getUserMedia resolver — que é o ponto
  ok(!globalThis.__mr, "o MediaRecorder não devia existir ainda");

  // ── 2. quando o microfone abre, vira gravação de verdade ─────
  soltaFluxo(); await flush(); await flush();
  ok(st()==="st-grav", "depois do microfone devia ser st-grav, veio " + st());
  ok(globalThis.__taxa===24000, "taxa de voz errada: " + globalThis.__taxa);

  // ── 3. o medidor ACOMPANHA o som (é o que prova que está ouvindo) ─
  // O `ouvir` já roda um quadro na hora, então a barra nunca fica em branco.
  // O que interessa aqui é ela SEGUIR a voz — barra parada num valor qualquer
  // enganaria igual a uma barra parada em zero.
  function rodar(n){ for(var i=0;i<n;i++){ var q=quadros.shift(); if(q) q(); } }
  function larg(){ return parseInt(el("nivel").style.width,10); }
  globalThis.__amplitude=1;  rodar(20); var quieto=larg();
  globalThis.__amplitude=30; rodar(20); var falando=larg();
  globalThis.__amplitude=1;  rodar(20); var quieto2=larg();
  ok(falando > quieto + 20, "a barra não subiu com a voz: " + quieto + "% → " + falando + "%");
  ok(quieto2 < falando - 20, "a barra não desceu no silêncio: " + falando + "% → " + quieto2 + "%");
  ok(quieto >= 2, "a barra não pode zerar: parece desligada (" + quieto + "%)");

  // ── 4. o TOQUE em enviar muda a tela na hora ─────────────────
  agora += 9000;
  el("manda").onclick();
  ok(st()==="st-env", "no toque de enviar devia ser st-env, veio " + st());
  ok(el("dica").textContent==="enviando…", "sem aviso de enviando: " + el("dica").textContent);
  ok(globalThis.__pediuStop, "não pediu pra parar o gravador");
  ok(envios.length===0, "não devia ter mandado nada antes do onstop");

  // ── 5. e a onda já está pronta: nada é decodificado ──────────
  globalThis.__mr.dispararStop();
  await flush(); await flush(); await flush();
  ok(globalThis.__decodeChamado===0,
     "decodificou o áudio depois do toque — é o que travava a tela");
  ok(envios.length===1, "não enviou: " + envios.length);
  ok(/seg=9/.test(envios[0].url), "duração errada na URL: " + envios[0].url);
  ok(!!envios[0].headers["X-Onda"], "a onda não foi junto");
  var onda=Buffer.from(envios[0].headers["X-Onda"],"base64");
  ok(onda.length===64, "a onda tem que ter 64 pontos, veio " + onda.length);
  ok(Math.max.apply(null,Array.from(onda))===100, "a onda não foi normalizada pelo pico");
  ok(Array.from(onda).some(function(x){return x<100;}), "a onda saiu chapada");

  // ── 6. sucesso não recarrega a página ───────────────────────
  ok(globalThis.__puxou, "não puxou a conversa");
  ok(!globalThis.__recarregou, "recarregou a página inteira");
  ok(!el("grav").classList.contains("on"), "a barra não fechou");
  ok(globalThis.__ctxFechado, "não fechou o AudioContext");

  // ── 7. cancelar também responde no toque ───────────────────
  globalThis.__pediuStop=false;
  el("mic").onclick(); soltaFluxo(); await flush(); await flush();
  el("cancela").onclick();
  ok(st()==="st-env", "no toque de cancelar devia trocar de estado, veio " + st());
  ok(el("dica").textContent==="cancelando…", "sem aviso de cancelando: " + el("dica").textContent);
  var n=envios.length;
  globalThis.__mr.dispararStop(); await flush(); await flush();
  ok(envios.length===n, "cancelar mandou o áudio assim mesmo");
  ok(!el("grav").classList.contains("on"), "a barra não fechou no cancelar");

  if(erros.length){ console.log("FALHOU: " + erros.join(" | ")); process.exit(1); }
  console.log("OK");
})();
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node não instalado")
def test_o_gravador_responde_no_toque_e_nao_depois(tmp_path):
    """A propriedade que a reclamação exige: entre o dedo tocar e a tela mudar não
    pode haver nada assíncrono. O harness segura o `getUserMedia` de propósito e
    confere o estado ANTES de soltar — se a tela só mudasse depois, o teste cai.

    Também prende que nada é decodificado no fim (a onda sai da gravação ao vivo)
    e que o sucesso não recarrega a página."""
    f = tmp_path / "gravador.mjs"
    f.write_text(_HARNESS.replace("__SCRIPT__", _script()), encoding="utf-8")
    p = subprocess.run(["node", str(f)], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "OK" in p.stdout


@pytest.mark.skipif(not shutil.which("node"), reason="node não instalado")
def test_o_javascript_do_gravador_compila():
    p = subprocess.run(["node", "--check", "-"], input=_script(),
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr


class _Req:
    """Request só com o que a tela lê (`_flash` mexe na sessão)."""
    def __init__(self):
        self.session = {}


def _tela(pode_voz=True):
    d = {"empresa": "Padaria", "cidade": "Teresina", "uf": "PI", "doc_fmt": "",
         "mensagens": [], "ia": False, "status": "novo", "etapas": [],
         "zap_link": "", "tel_link": ""}
    return pc._lead_vendedor(_Req(), 7, d, pode_voz=pode_voz).body.decode()


def test_a_barra_nasce_com_o_aviso_de_preparando():
    """O texto do primeiro estado vem no HTML SERVIDO, não do JS: se o script
    demorar a rodar, a barra ainda diz a coisa certa em vez de um "gravando"
    mentiroso — que é exatamente o tipo de mentira que fez parecer travado."""
    h = _tela()
    assert "id=dica>preparando…" in h
    assert "gravando…" not in h


def test_sem_o_canal_certo_nao_vai_gravador_nenhum_pra_tela():
    """O microfone é do canal QR. Sem ele, nem o botão nem o script sobem — e o
    peso do gravador não viaja pro celular de quem não vai usar."""
    h = _tela(pode_voz=False)
    assert "id=mic" not in h and "id=grav" not in h
    assert "getUserMedia" not in h


def test_o_css_esconde_o_relogio_enquanto_prepara():
    """Relógio correndo antes de a gravação começar contaria tempo que não existe."""
    css = pc._CSS
    assert ".gravando .rel{" in css and "display:none" in css.split(".gravando .rel{")[1][:120]
    assert ".gravando.st-grav .rel" in css


def test_o_medidor_so_aparece_gravando():
    """Ele é a prova de que o microfone está ouvindo — fora da gravação não tem o
    que medir, e uma barra parada sugeriria que travou."""
    css = pc._CSS
    assert ".gravando .nivel{" in css and "display:none" in css.split(".gravando .nivel{")[1][:120]
    assert ".gravando.st-grav .nivel{display:block}" in css
