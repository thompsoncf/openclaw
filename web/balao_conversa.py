"""O balão de conversa — o mesmo em toda tela que precisa mostrar um diálogo.

Nasceu no funil de Prospecção (`kbAbrirChat`): um popover que abre ancorado no
botão 💬, busca as mensagens em `/painel/prospeccao/comunicacao/thread/<id>` e
deixa responder ali mesmo, sem sair da tela.

POR QUE VIROU MÓDULO (07/09/2026). O Raio-X ganhou a lista de quem está
pendente, e o 💬 de lá NAVEGAVA pra Comunicação — pra voltar, recarregava o
Raio-X inteiro e perdia a lista aberta. O dono pediu o balão do funil ali
também. Copiar seria a segunda cópia de ~115 linhas de posicionamento,
fechamento (Esc, clique fora, rolagem) e composer: duas cópias divergem, e o
que já custou caro aqui — o balão nascendo fora da tela — voltaria só num lado.

QUEM USA: `web/painel_prospeccao.py` (funil) e `web/painel_raio_x.py`. As duas
injetam CSS e JS pelas globais do Jinja registradas em `registrar_jinja`, o
mesmo caminho que `finance/marca.py` já usa.

O QUE A TELA PRECISA TER: um botão que chame
`kbAbrirChat(evento, conversa_id, aba, botao, nome)`. `aba` é 'conversas'
(WhatsApp/Instagram) ou 'emails'; `nome` é opcional — sem ele o balão procura
no card do funil (`.kbcard .emp`).

O QUE NÃO ESTÁ AQUI, de propósito: o balão do LEAD (`kbAbrirLead`), o
"Ler as conversas" e o `kbPerguntarData` — são do funil e continuam lá, o
último porque depende de uma variável que só aquela tela tem.
"""
from markupsafe import Markup

CSS = """/* o balão do chat — SÓ mensagens, nada do resto do hub de Comunicação. Nasce em
   fixed (calculado em JS a partir do botão), então o CSS aqui não precisa
   posicionar nada em relação ao card — só desenhar o balão em si. */
.chatpop{position:fixed;z-index:90;width:336px;max-width:calc(100vw - 16px);max-height:70vh;
  background:var(--card);border:1px solid var(--borda);border-radius:14px;overflow:hidden;
  box-shadow:0 18px 46px rgba(0,0,0,.5);display:flex;flex-direction:column}
/* ✕ de fechar — fora do fluxo do cabeçalho, cravado no canto (não empurrado por
   flex): o título nunca o empurra pra baixo/quebra de linha, e fica no mesmo
   lugar sempre, em qualquer balão (chat ou resumo do lead). `margin:0` vence o
   `button{width:100%;margin-top:1.4rem}` global (pros botões de formulário de
   login/cadastro) — sem isso o botão herdava 1.4rem de margem e nascia ~22px
   mais abaixo do canto. Mesma causa da busca da Comunicação (✕ que esmagava
   o campo) — dessa vez pegou ANTES de virar bug visível. */
.pop-close{position:absolute;top:.5rem;right:.5rem;z-index:2;width:24px;height:24px;
  margin:0;display:flex;align-items:center;justify-content:center;padding:0;
  background:var(--card-2);border:1px solid var(--borda);border-radius:50%;
  color:var(--txt-mut);cursor:pointer;font-size:.72rem;line-height:1}
.pop-close:hover{color:var(--txt);border-color:var(--coral);background:rgba(224,87,79,.14)}
.cp-h{display:flex;align-items:center;gap:.5rem;padding:.55rem 2.1rem .55rem .7rem;border-bottom:1px solid var(--borda);flex:none}
.cp-h .av{width:26px;height:26px;border-radius:8px;background:#13251d;color:var(--verde-claro);
  display:flex;align-items:center;justify-content:center;font-weight:700;font-size:.68rem;flex:none}
.cp-h b{font-size:.85rem}
.cp-h small{display:block;color:var(--txt-mut);font-size:.68rem}
.cp-mais{display:block;text-align:center;padding:.42rem;font-size:.72rem;color:var(--txt-mut);
  border-top:1px solid var(--borda);text-decoration:none;flex:none}
.cp-mais:hover{color:var(--verde-claro)}
.cx-empty{padding:1.6rem 1rem;text-align:center;color:var(--txt-mut);font-size:.84rem}
.cx-msgs{flex:1;overflow-y:auto;padding:.7rem;display:flex;flex-direction:column;gap:.45rem;min-height:120px}
.cx-m{max-width:85%;align-self:flex-end;background:#123028;border:1px solid #1d5741;border-radius:12px;
  border-bottom-right-radius:4px;padding:.42rem .6rem;font-size:.82rem;line-height:1.42}
.cx-m .meta{display:block;color:var(--txt-mut);font-size:.64rem;margin-top:.25rem;text-align:right}
.cx-m.cin{align-self:flex-start;background:var(--card-2);border-color:var(--borda)}
.cx-m.cbot{align-self:flex-start;background:#1c1428;border-color:#4a3163}
.cx-comp{border-top:1px solid var(--borda);padding:.5rem .6rem;display:flex;gap:.4rem;align-items:flex-end;flex:none}
.cx-comp textarea{flex:1;resize:none;background:var(--bg);border:1px solid var(--borda);color:var(--txt);
  border-radius:9px;padding:.4rem .55rem;font:inherit;font-size:.8rem;height:2.1rem;margin:0;width:auto}
.cx-comp button{background:var(--verde);color:var(--sobre-verde);border:0;border-radius:8px;
  padding:0 .8rem;font-weight:600;cursor:pointer;font-size:.8rem;width:auto;margin:0;flex:none}
.cx-stub{border-top:1px solid var(--borda);padding:.55rem .7rem;color:var(--txt-mut);font-size:.76rem;flex:none}
.cx-stub .lbl2{display:inline-block;font-size:.6rem;padding:.04rem .38rem;border-radius:999px;
  background:#241634;color:#c9a3e0;border:1px solid #4a3163;margin-left:.3rem}"""

JS = """var _chatPop=null;
function kbFecharChat(){
  if(_chatPop){_chatPop.remove();_chatPop=null;}
  document.removeEventListener('click',_chatPopFora,true);
  document.removeEventListener('keydown',_chatPopEsc,true);
  window.removeEventListener('scroll',_chatPopRolou,true);
}
function _chatPopFora(e){if(_chatPop&&!_chatPop.contains(e.target))kbFecharChat();}
function _chatPopEsc(e){if(e.key==='Escape')kbFecharChat();}
// Rolar a página SOME com o balão, em vez de deixá-lo pra trás flutuando longe
// do card que ele veio. É `fixed` (não anda com a rolagem por design — foge do
// corte de overflow-x:auto da coluna), então "ficar pendurado no lugar errado"
// era pior que só fechar: fechado, o próximo clique abre de novo já no lugar
// certo. `capture:true` pega rolagem de QUALQUER contêiner (a coluna do
// kanban rola sozinha), não só da janela — scroll não borbulha por padrão.
//
// MAS: a lista de mensagens rola SOZINHA assim que abre, pra mostrar a
// última (`box.scrollTop=box.scrollHeight`, logo abaixo) — e rolar dentro dela
// pra ler o histórico é uso normal do balão, não um "saiu daqui". Sem o
// `contains` abaixo, o balão se fechava sozinho no instante em que as
// mensagens chegavam: o auto-scroll interno disparava este mesmo listener.
function _chatPopRolou(e){if(_chatPop&&_chatPop.contains(e.target))return;kbFecharChat();}
function cxEscK(s){var d=document.createElement('div');d.textContent=(s==null?'':s);return d.innerHTML;}
// Igual ao cxMsgsHtml do hub, só que enxuto: sem selo de entrega, sem cabeçalho
// de campanha — o balão é pra LER a conversa, não pra operar ela.
function kbMsgsHtml(d){
  if(!d.msgs||!d.msgs.length)return '<div class="cx-empty">Sem mensagens.</div>';
  var h='';
  d.msgs.forEach(function(m){
    var cls=(m.direcao==='in')?'cx-m cin':((m.autor==='bot')?'cx-m cbot':'cx-m');
    var corpo=cxEscK(m.corpo||m.cabecalho||'').replace(/\\n/g,'<br>');
    h+='<div class="'+cls+'">'+corpo+'<span class="meta">'+cxEscK(m.quem||'')+' · '+cxEscK(m.quando||'')+'</span></div>';
  });
  return h;
}
function kbAbrirChat(ev,convId,aba,btn,nome){
  ev.stopPropagation();
  kbFecharChat();
  // só um balão por vez — chat e resumo do lead se excluem. O balão do LEAD é
  // do funil; no Raio-X ele não existe, daí o guarda.
  if(window.kbFecharLead)window.kbFecharLead();
  // O NOME vem de quem chama. O funil não passava nada e o balão ia buscar no
  // card (`.kbcard .emp`); o Raio-X não tem card nenhum, então passa direto.
  var card=btn.closest('.kbcard');
  var nm=nome||(card?(card.querySelector('.emp')||{}).textContent:'');
  var r=btn.getBoundingClientRect();
  var pop=document.createElement('div');
  pop.className='chatpop';
  // Cabe na tela SEMPRE — é o bug que motivou isto: nascendo fixo em
  // `top:botão+6` sem olhar o que sobrava embaixo, um card perto do fim da
  // janela abria o balão parcialmente fora da tela. E como é `position:fixed`,
  // rolar a PÁGINA não revela o que ficou cortado (fixed não se move com a
  // rolagem) — por fora parecia "trava e não deixa ver as mensagens". Aqui
  // mede o espaço disponível pra cima e pra baixo do botão e escolhe o lado
  // com mais folga, com a altura do balão presa a esse espaço (a lista de
  // mensagens rola por dentro, `.cx-msgs{overflow-y:auto}` — o balão inteiro
  // continua sempre visível de ponta a ponta).
  var MARG=8, GAP=6, LARG=336;
  var abaixo=window.innerHeight-r.bottom-GAP-MARG, acima=r.top-GAP-MARG;
  if(abaixo>=220||abaixo>=acima){
    pop.style.top=(r.bottom+GAP)+'px';
    pop.style.maxHeight=Math.max(160,Math.min(460,abaixo))+'px';
  }else{
    pop.style.bottom=(window.innerHeight-r.top+GAP)+'px';
    pop.style.maxHeight=Math.max(160,Math.min(460,acima))+'px';
  }
  pop.style.left=Math.max(MARG,Math.min(r.left,window.innerWidth-LARG-MARG))+'px';
  pop.innerHTML='<button type="button" class="pop-close" title="Fechar" onclick="kbFecharChat()">✕</button>'
    +'<div class="cp-h"><span class="av">'+cxEscK((nm||'?').trim().slice(0,2).toUpperCase())+'</span>'
    +'<div><b>'+cxEscK(nm||'Conversa')+'</b><small>'+cxEscK(btn.title||'')+'</small></div></div>'
    +'<div class="cx-msgs" id="cp-msgs"><div class="cx-empty">Carregando…</div></div>'
    +'<div id="cp-comp"></div>'
    +'<a class="cp-mais" target="_blank" href="/painel/prospeccao/comunicacao?aba='+aba+'&abrir='+convId+'">Ver conversa completa ↗</a>';
  document.body.appendChild(pop);
  _chatPop=pop;
  // no mesmo clique que abriu, senão o próprio clique do botão já contaria como
  // "fora" e fecharia o balão antes de ele aparecer.
  setTimeout(function(){document.addEventListener('click',_chatPopFora,true);document.addEventListener('keydown',_chatPopEsc,true);
    window.addEventListener('scroll',_chatPopRolou,true);},0);
  fetch('/painel/prospeccao/comunicacao/thread/'+convId).then(function(r){return r.json();}).then(function(d){
    if(_chatPop!==pop)return;   // o popover foi trocado/fechado antes da resposta chegar
    var box=pop.querySelector('#cp-msgs');
    box.innerHTML=d.ok?kbMsgsHtml(d):'<div class="cx-empty">Não consegui abrir.</div>';
    box.scrollTop=box.scrollHeight;
    var comp=pop.querySelector('#cp-comp');
    if(d.pode_responder){
      comp.innerHTML='<div class="cx-comp"><textarea id="cp-input" rows="1" placeholder="Escreva uma resposta…"'
        +' onkeydown="if(event.key===\\'Enter\\'&&!event.shiftKey){event.preventDefault();kbResponderChat('+convId+');}"></textarea>'
        +'<button type="button" onclick="kbResponderChat('+convId+')">Enviar</button></div>';
      // o "perguntar" do card deixa a pergunta pronta na caixa — o vendedor
      // confere o tom e manda (decisão do dono, 04/09: abre, não dispara)
      if(_cpPrefill){var ta=comp.querySelector('#cp-input');if(ta){ta.value=_cpPrefill;ta.rows=2;ta.focus();ta.setSelectionRange(ta.value.length,ta.value.length);}}
    }else if(d.ok){
      comp.innerHTML='<div class="cx-stub">Responder por aqui <span class="lbl2">em breve</span></div>';
    }
    _cpPrefill='';
  }).catch(function(){
    if(_chatPop===pop)pop.querySelector('#cp-msgs').innerHTML='<div class="cx-empty">Falha de rede.</div>';
  });
}
var _cpPrefill='';
function kbResponderChat(convId){
  if(!_chatPop)return;
  var ta=_chatPop.querySelector('#cp-input');
  var texto=(ta.value||'').trim();
  if(!texto)return;
  ta.disabled=true;
  var body=new URLSearchParams();body.append('conversa_id',convId);body.append('texto',texto);
  fetch('/painel/prospeccao/comunicacao/responder',{method:'POST',body:body}).then(function(r){return r.json();}).then(function(d){
    if(!_chatPop)return;
    ta.disabled=false;
    if(!d.ok){alert(d.erro||'Não consegui enviar.');return;}
    ta.value='';
    fetch('/painel/prospeccao/comunicacao/thread/'+convId).then(function(r){return r.json();}).then(function(d2){
      if(!_chatPop)return;
      var box=_chatPop.querySelector('#cp-msgs');
      if(box&&d2.ok){box.innerHTML=kbMsgsHtml(d2);box.scrollTop=box.scrollHeight;}
    });
  }).catch(function(){if(ta)ta.disabled=false;alert('Erro de conexão.');});
}"""


def registrar_jinja(env) -> None:
    """Deixa `balao_css` e `balao_js` disponíveis em qualquer template do env.

    `Markup` pra o bloco sair cru mesmo onde o autoescape estiver ligado: o JS
    tem aspas e `&&` que viram entidade e quebram o script sem avisar.
    """
    env.globals["balao_css"] = Markup(CSS)
    env.globals["balao_js"] = Markup(JS)
