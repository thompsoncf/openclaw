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
// O zapFetch (web/zap_fetch.py) entrega o CORPO, não a Response — e `null`
// quando a troca falhou, já tendo avisado a pessoa. Mesmo log: o que estes
// testes medem é o que foi PEDIDO, e isso não mudou com o caminho.
globalThis.zapFetch = function(url, opt){
  opt = opt || {};
  log.fetch.push({url: url, headers: opt.headers, corpo: String(opt.body)});
  return Promise.resolve(resposta === "rede" ? null : resposta);
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


def _tela_da_conversa(texto_pre: str = "", papel: str = "dono") -> str:
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
         "mensagens": [{"id": 10, "who": "in", "texto": "Oi, tem data?", "quando": agora,
                        "status": ""},
                       {"id": 11, "who": "out", "texto": "Tem sim!", "quando": agora,
                        "status": "lido"}]}
    # o papel decide o que a folha de respostas rápidas oferece: quem manda na
    # conta apaga as da equipe, o vendedor só usa
    req = SimpleNamespace(session={"papel": papel}, query_params=QueryParams(
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


def test_o_carimbo_de_entrega_aparece_colado_na_hora():
    """Como no WhatsApp: ✓ saiu, ✓✓ chegou, ✓✓ azul foi lido — ao lado da hora, e
    só nas NOSSAS mensagens."""
    assert pc._ticks("enviado") == "<i class=ck title='enviado'>✓</i>"
    assert pc._ticks("entregue") == "<i class=ck title='entregue'>✓✓</i>"
    assert "lido" in pc._ticks("lido") and "✓✓" in pc._ticks("lido")
    assert pc._ticks("") == "" and pc._ticks("qualquer_coisa") == ""

    html = _tela_da_conversa()
    dela, minha = html.split("Oi, tem data?")[1], html.split("Tem sim!")[1]
    assert "ck lido" in minha[:120], "a minha leva o carimbo"
    assert "ck" not in dela[:80], "a do cliente, não"
    # e o polling desenha igual: a tabela do JS é a mesma do servidor
    assert "s==='lido'" in html and "s==='entregue'" in html and "s==='enviado'" in html
    assert "j.estados||[]" in html, "o ✓ vira ✓✓ sem mensagem nova"


def test_o_audio_sai_na_hora_e_a_transcricao_alcanca_depois():
    """O áudio esperava a transcrição pra sair: o vendedor gravava, tocava em
    enviar e ficava olhando a barra enquanto o servidor falava com o serviço de
    voz. Agora o envio responde primeiro, a transcrição roda depois da resposta, e
    o texto alcança a bolha que já está na tela."""
    import inspect
    from finance import cockpit as ck
    rota = inspect.getsource(pc.cockpit_lead_audio)
    assert "run_in_threadpool(_audio_sync" in rota, "o envio sai do event loop"
    assert "BackgroundTask(_transcrever_sync" in rota, "a transcrição vai DEPOIS da resposta"
    assert '_pendente' in rota and "r.pop(" in rota, "os bytes não vão pra tela"
    # e o envio em si não fala mais com o STT
    envio = inspect.getsource(ck.enviar_audio)
    assert "transcritor_se_configurado" not in envio
    assert "transcritor_se_configurado" in inspect.getsource(ck.transcrever_audio)
    # a tela sabe trocar o texto de uma bolha que já existe
    html = _tela_da_conversa()
    assert "if(e.texto)" in html and "createTextNode(e.texto)" in html


def test_o_audio_toca_na_bolha_com_onda_e_tempo():
    """O controle nativo do navegador saía diferente em cada aparelho — no iPhone
    do dono virou "00:08 ——— 00:00", que não diz nada. Agora a bolha tem tocar,
    onda e duração, e a onda é a DE VERDADE quando ela existe."""
    som = pc._som_html("/cockpit/lead/7/midia/99", 6, [10, 90, 50])
    assert "class=toca" in som and "aria-label='Tocar áudio'" in som
    assert som.count("<i style='height:") == 3 and "height:90%" in som
    assert "<span class=dur>0:06</span>" in som
    assert "<audio preload=none src='/cockpit/lead/7/midia/99'>" in som
    # sem onda, as barras ficam iguais: régua de progresso, não fala inventada
    lisa = pc._som_html("/x", 3)
    assert lisa.count("height:34%") == len(pc._ONDA_LISA)
    # e a bolha de mídia do tipo áudio usa o tocador
    assert "class='mid som'" in pc._midia_html(7, {"id": 5, "midia": {"tipo": "audio", "segundos": 6}})

    html = _tela_da_conversa()
    assert "function som(s,seg,onda)" in html, "o polling desenha o mesmo tocador"
    assert "window.__som=som" in html, "o gravador reaproveita o tocador"
    assert "closest('.som .toca')" in html and "a.play()" in html
    assert "if(o!==a&&!o.paused){o.pause()" in html, "um áudio de cada vez"
    # o gravador desenha a bolha com a onda que acabou de medir
    assert "window.__som?window.__som(url, seg, pontos)" in pc._VOZ_JS


def test_as_respostas_rapidas_escrevem_na_caixa_e_nao_enviam():
    """Mandar no toque economizaria um segundo e custaria o dia em que o preço de
    uma festa sai pro cliente de outra."""
    html = _tela_da_conversa()
    assert "id=rapidas" in html and "Respostas rápidas" in html
    assert "id=resplista" in html and "Salvar o que está escrito" in html
    js = pc._RAPIDAS_JS
    assert "caixa.value=(atual" in js, "escreve na caixa"
    assert "form.composer" in js and ".submit()" not in js and "requestSubmit" not in js
    # a folha abre por JS, não por âncora: `:target` rola o .wrap e empurra a tela
    assert ":target" not in js and "folha.hidden=false" in js
    # o atalho da barra
    assert "/([^\\\\/\\\\s][^\\\\/]*)?$/" in js.replace("\\", "\\\\") or "match(/(?:^|\\s)\\//" in js


def test_quem_nasce_hidden_precisa_da_regra_hidden_no_css():
    """A ARMADILHA QUE PEGOU AS RESPOSTAS RÁPIDAS (20/09/2026).

    O atributo `hidden` esconde pelo estilo do navegador (`display:none`), e
    QUALQUER `display` escrito na folha de estilo ganha dele. A folha das
    respostas tinha `display:flex` e nasceu aberta por cima da caixa de mensagem:
    o ✕ não fechava nada e o vendedor digitava na busca achando que era o campo
    de responder.

    `.lupa` e `.abertura` já carregavam a regra `[hidden]` por isso. Este teste
    cobra a regra de TODO elemento que a tela desenha escondido — a próxima folha
    não vai repetir o erro.
    """
    css = pc._CSS_TEXTO
    html = _tela_da_conversa()
    achados = []
    for tag in re.findall(r"<div[^>]*\bhidden\b[^>]*>", html):
        m = re.search(r"class=['\"]?([\w\- ]+)", tag)
        if not m:
            continue
        for classe in m.group(1).split():
            tem_display = re.search(r"\.%s\{[^}]*display:" % re.escape(classe), css)
            tem_regra = f".{classe}[hidden]" in css
            if tem_display and not tem_regra:
                achados.append(classe)
    assert not achados, (
        "estes nascem escondidos mas o CSS manda desenhar: "
        + ", ".join(sorted(set(achados)))
        + " — falta `.classe[hidden]{display:none}`")


def test_a_folha_explica_o_que_e_da_empresa_e_o_que_e_seu():
    """A folha mistura as da empresa com as do vendedor. Sem uma linha dizendo
    qual é qual, ele lê a diferença (umas com ✕, outras com 🔒) como defeito."""
    vendedor = _tela_da_conversa(papel="vendedor").split("class=respdica")[1][:340]
    assert "da empresa" in vendedor and "não apaga" in vendedor and "🔒" in vendedor
    assert "suas" in vendedor and "só você" in vendedor

    dono = _tela_da_conversa(papel="dono").split("class=respdica")[1][:340]
    assert "pode apagar" in dono, "quem manda na conta precisa saber que pode"
    assert "ninguém mais vê" in dono
    assert ".respdica{" in pc._CSS_TEXTO


def test_a_resposta_da_equipe_mostra_cadeado_em_vez_de_buraco():
    """Relatado com print: "o ✕ só aparece na primeira". Era a regra funcionando —
    a resposta da equipe é a mesma pros quatro vendedores, e apagá-la some com ela
    pra todo mundo, então só dono ou gestor apaga. O que faltava era a tela DIZER
    isso: sem o ✕ sobrava um buraco mudo do lado, que parece defeito."""
    js = pc._RAPIDAS_JS
    assert "data-trava=1" in js and "só o dono ou o gestor apaga" in js
    assert 'alvo.getAttribute("data-trava")' in js, "o cadeado não abre o confirm"
    assert ".respdel[data-trava]" in pc._CSS_TEXTO
    # e quem manda na conta continua vendo o ✕ em todas
    assert "(x.equipe&&!podeEquipe)" in js


def test_o_botao_de_salvar_nao_mente():
    """Ele mostra O QUE vai salvar e nasce apagado: com a folha por cima da
    conversa o vendedor não vê mais o que escreveu, e um botão que só responde
    com um aviso ("escreva a mensagem") é um botão que mente."""
    html = _tela_da_conversa()
    assert "id=respsalvar disabled" in html
    assert "Escreva na caixa pra salvar" in html
    js = pc._RAPIDAS_JS
    assert "function acertarSalvar()" in js and "s.disabled=!txt" in js
    assert "'Salvar: “'" in js, "o botão diz o que vai guardar"
    assert "alert(\"Escreva a mensagem" not in js, "o aviso virou botão apagado"
    # e a busca não pode ser confundida com a caixa de responder
    assert "🔎 Procurar nas respostas" in html


def test_fechar_a_folha_esconde_o_fundo_e_devolve_o_teclado():
    js = pc._RAPIDAS_JS
    fechar = js.split("function fechar()")[1][:400]
    assert "folha.hidden=true" in fechar and "fundo0.hidden=true" in fechar
    assert "caixa.focus()" in fechar


def test_a_folha_de_respostas_fica_fora_do_form():
    """Um <button> solto dentro de um form envia o form no Enter — e o form daqui
    é o que manda mensagem pro cliente."""
    html = _tela_da_conversa()
    depois_do_form = html.split("</form>")[1]
    assert "id=resp " in depois_do_form or "id=resp>" in depois_do_form


def test_o_microfone_e_um_microfone_desenhado_cheio():
    """O traço fino sumia no botão de 40px: no celular do dono aparecia um
    retângulo sem o arco, com cara de ícone quebrado."""
    html = _tela_da_conversa()
    bt = html.split("id=mic")[1][:600]
    assert "fill=currentColor" in bt and "stroke" not in bt
    assert bt.count("<path") == 2, "corpo do microfone + a base"


def test_a_folha_de_acoes_nao_empurra_a_tela_pra_cima():
    """19/09/2026, iPhone: tocar em "Ficha, funil e fechamento" jogava a tela
    inteira pra cima — o topo da conversa sumia, sobrava preto embaixo e a folha
    mostrava o FIM do conteúdo.

    O salto de âncora é a causa: `#acoes` é a própria folha, e trazer o alvo à
    vista faz o navegador rolar o `.wrap` (rolagem programática atravessa o
    `overflow:hidden`). O `:target` continua abrindo a folha sem JS; o script só
    desfaz a rolagem."""
    html = _tela_da_conversa()
    assert "id=acoes" in html and ".folha:target" in pc._CSS_TEXTO
    assert "w.scrollTop=0" in html and "fo.scrollTop=0" in html
    assert "hashchange" in html


def test_a_bolha_de_verdade_toma_o_lugar_da_otimista():
    """O polling traz a mensagem gravada; a otimista, com o mesmo texto, sai."""
    fonte = __import__("inspect").getsource(pc._lead_vendedor)
    assert ".bub.voando[data-txt]" in fonte and "vs[k].remove()" in fonte
    assert "getAttribute('data-txt')===m.texto" in fonte
