"""A janela do lead — a mesma em toda tela que precisa decidir o que fazer com um.

Nasceu no funil de Prospecção (`kbAbrirLead`): um popover ancorado no card, que
busca o resumo em `/painel/prospeccao/<id>/resumo` e deixa ver os dados, corrigir
o cadastro, ler o histórico e **mudar a situação no funil** sem sair da tela.

POR QUE VIROU MÓDULO (16/09/2026). Pedido do dono, depois de trabalhar a fila:

    "dentro do follow tem um botao abrir ficha e mudar o status pode ser por la
     e so deixar abrir uma janela igual tem no funil"

O Follow-up tinha "Abrir ficha", que NAVEGA pra ficha completa: pra voltar,
recarrega a lista inteira e perde o lugar onde a pessoa estava — e o que ele
precisa ali é pequeno, "este já fechou, muda de contato pra ganho". O balão de
conversa (`web/balao_conversa.py`) já tinha percorrido esse caminho em 07/09 pelo
mesmo motivo, e o docstring de lá dizia, sobre esta janela: "é do funil e continua
lá". Continuou até existir o segundo dono. Agora existe.

QUEM USA: `web/painel_prospeccao.py` (funil) e `web/painel_follow_up.py`. As duas
injetam CSS e JS pelas globais do Jinja registradas em `registrar_jinja`, o mesmo
caminho do balão.

O QUE A TELA PRECISA TER, e as duas coisas são obrigatórias:

1. um botão que chame `kbAbrirLead(evento, lead_id, elemento)` — o `elemento` é o
   que a janela mede pra se posicionar (o card no funil, o botão no Follow-up);
2. `var _KB_STATUS = {{ status|tojson }}` — a lista `[[chave, rótulo], ...]` das
   etapas DA CONTA. É ela que enche o seletor de situação. Sem ela o seletor sai
   vazio e a janela vira só leitura; por isso a leitura aqui é `window._KB_STATUS`
   e não `_KB_STATUS` cru, que estouraria um ReferenceError e mataria o clique
   inteiro por causa de um seletor.

O GANCHO `window.kbDepoisDoStatus(d, id, novo)`, opcional: o que a tela faz depois
de a situação mudar. O funil move o card pra coluna nova sem recarregar; quem não
define o gancho recarrega a página, que é o certo pra uma lista que pode ter
perdido o lead (ganho e perdido saem do funil).

O QUE NÃO ESTÁ AQUI, de propósito: `kbPerguntarData` e `kbLerConversas` continuam
no funil — o primeiro depende de uma variável que só aquela tela tem, e o segundo
é um botão do cabeçalho do quadro, não da janela.
"""
from markupsafe import Markup

CSS = """/* o balão do LEAD — resumo pra decidir a próxima ação (contato, valor, situação,
   últimas atividades). Mesma engenharia do balão de chat: nasce fixed, medido
   do próprio card, sem carregar a ficha inteira num iframe. Edição de cadastro,
   IA, decisor e orçamento continuam só na ficha completa (link no rodapé). */
.leadpop{position:fixed;z-index:90;width:378px;max-width:calc(100vw - 16px);max-height:70vh;
  background:var(--card);border:1px solid var(--borda);border-radius:14px;overflow:hidden;
  box-shadow:0 18px 46px rgba(0,0,0,.5);display:flex;flex-direction:column}
.lp-h{padding:.75rem 2.1rem .65rem .85rem;border-bottom:1px solid var(--borda);flex:none}
.lp-h .top{display:flex;align-items:center;gap:.45rem;flex-wrap:wrap}
.lp-h h3{font-size:1rem;margin:0}
.lp-h .sub{color:var(--txt-mut);font-size:.78rem;margin-top:.2rem}
.lp-canais{display:flex;gap:.35rem;flex-wrap:wrap;margin-top:.5rem}
.lp-canal{display:inline-flex;align-items:center;gap:.25rem;font-size:.7rem;padding:.14rem .5rem;border-radius:999px;
  border:1px solid var(--verde);background:rgba(62,224,166,.10);color:var(--verde-claro)}
.lp-acoes{display:flex;gap:.4rem;flex-wrap:wrap;align-items:center;padding:.65rem .85rem;border-bottom:1px solid var(--borda);flex:none}
.lp-ab{background:var(--neon-fundo);border:1px solid var(--neon-borda);border-radius:8px;padding:.32rem .65rem;
  font-size:.78rem;color:var(--txt);cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;gap:.3rem}
.lp-ab:hover{border-color:var(--verde)}
.lp-status{margin-left:auto}
.lp-status select{background:var(--bg);border:1px solid var(--borda);color:var(--txt);border-radius:999px;
  padding:.28rem .6rem;font-size:.76rem;width:auto;margin:0}
.lp-body{padding:.7rem .85rem;overflow-y:auto;flex:1}
.lp-sh{display:flex;align-items:center;gap:.5rem;margin-bottom:.5rem}
.lp-sh b{font-size:.68rem;text-transform:uppercase;letter-spacing:.06em;color:var(--txt-mut)}
/* margin:0 0 0 auto (não só margin-left:auto) — sem zerar o topo/baixo, o
   `button{margin-top:1.4rem}` global vazava e empurrava o botão ~22px pra
   baixo dentro de ".lp-sh" (mesma causa do ✕ de excluir e dos ✕ dos balões). */
.lp-edit-btn{margin:0 0 0 auto;background:none;border:1px solid var(--borda);color:var(--txt-mut);border-radius:7px;
  padding:.15rem .5rem;font-size:.7rem;cursor:pointer;width:auto}
.lp-edit-btn:hover{color:var(--txt);border-color:var(--verde)}
.lp-grid{display:grid;grid-template-columns:1fr 1fr;gap:.5rem .8rem;font-size:.8rem}
.lp-grid .k{color:var(--txt-mut);font-size:.68rem;text-transform:uppercase;letter-spacing:.04em}
.lp-grid .v{margin-top:.1rem;color:var(--txt)}
.lp-grid .full{grid-column:1 / -1}
.lp-sec2{margin-top:.9rem}
.lp-ativ{display:flex;gap:.5rem;padding:.4rem 0;border-top:1px solid var(--borda);font-size:.8rem}
.lp-ativ:first-child{border-top:0}
.lp-ativ .dot2{width:7px;height:7px;border-radius:50%;margin-top:.4rem;flex-shrink:0}
.lp-ativ .qd{color:var(--txt-mut);font-size:.72rem}
.lp-mais{display:block;text-align:center;padding:.55rem;font-size:.76rem;color:var(--txt-mut);
  border-top:1px solid var(--borda);text-decoration:none;flex:none}
.lp-mais:hover{color:var(--verde-claro)}
.lp-ed-grid{display:grid;grid-template-columns:1fr 1fr;gap:.55rem .7rem}
.lp-ed-grid .full{grid-column:1 / -1}
.lp-ed-grid label{display:block;font-size:.68rem;text-transform:uppercase;letter-spacing:.04em;color:var(--txt-mut);margin-bottom:.2rem}
.lp-ed-grid input,.lp-ed-grid textarea{width:100%;background:var(--bg);border:1px solid var(--borda);color:var(--txt);
  border-radius:8px;padding:.35rem .55rem;font:inherit;font-size:.8rem;box-sizing:border-box;margin:0}
.lp-ed-grid textarea{resize:vertical;min-height:2.2rem}
.lp-ed-acoes{display:flex;gap:.5rem;margin-top:.8rem}
.lp-ed-acoes button{width:auto;margin:0;font-size:.8rem;cursor:pointer}
.lp-ed-salvar{background:var(--verde);color:var(--sobre-verde);border:0;border-radius:8px;padding:.4rem .9rem;font-weight:600}
.lp-ed-cancelar{background:none;border:1px solid var(--borda);color:var(--txt-mut);border-radius:8px;padding:.4rem .9rem}
.lp-trecho{border-left:2px solid #1f3a4d;padding:.25rem .55rem;margin-top:.3rem;color:var(--txt-mut);font-style:italic;font-size:.78rem;background:var(--bg)}
.lp-pista{margin-top:.3rem;font-size:.78rem;color:#e0b45f}
.lp-evbts{display:flex;gap:.4rem;margin-top:.45rem}
.lp-evbts .pbtn{width:auto;margin:0;padding:.35rem .7rem;font-size:.78rem}"""

JS = r"""// confirmar o que o leitor achou: vira "✓ confirmado" no card
function kbLeadConfirmarEvento(id){
  fetch('/painel/prospeccao/'+id+'/evento/confirmar',{method:'POST',headers:{'X-Requested-With':'fetch'},body:new FormData()})
    .then(function(r){return r.json();}).then(function(d){if(!d.ok){alert(d.erro||'Não consegui confirmar.');return;}location.reload();})
    .catch(function(){alert('Falha de rede.');});}
// O balão do LEAD — resumo pra decidir a próxima ação (ligar, chamar no
// WhatsApp, mudar a situação, ver o que aconteceu por último). Antes o clique
// no card abria uma gaveta de 1080px com a ficha INTEIRA num iframe (edição de
// cadastro, IA de primeiro contato, decisor Credify, orçamento) — pesado pra
// só decidir o que fazer agora. Isso fica só na ficha completa, atrás do link
// "Ver ficha completa". Mesmo mecanismo de posicionamento/fechar do balão de
// chat (ver comentário em kbAbrirChat) — duplicado de propósito, não
// compartilhado: são popovers independentes, cada um fecha só o que é seu.
var _leadPop=null;
function kbFecharLead(){
  if(_leadPop){_leadPop.remove();_leadPop=null;}
  document.removeEventListener('click',_leadPopFora,true);
  document.removeEventListener('keydown',_leadPopEsc,true);
  window.removeEventListener('scroll',_leadPopRolou,true);
}
function _leadPopFora(e){if(_leadPop&&!_leadPop.contains(e.target))kbFecharLead();}
function _leadPopEsc(e){if(e.key==='Escape')kbFecharLead();}
function _leadPopRolou(e){if(_leadPop&&_leadPop.contains(e.target))return;kbFecharLead();}
function kbAbrirLead(ev,id,cardEl){
  if(ev)ev.stopPropagation();
  kbFecharChat();
  kbFecharLead();
  var r=cardEl.getBoundingClientRect();
  var pop=document.createElement('div');
  pop.className='leadpop';
  var MARG=8, GAP=6, LARG=378;
  var abaixo=window.innerHeight-r.bottom-GAP-MARG, acima=r.top-GAP-MARG;
  if(abaixo>=260||abaixo>=acima){
    pop.style.top=(r.bottom+GAP)+'px';
    pop.style.maxHeight=Math.max(200,Math.min(560,abaixo))+'px';
  }else{
    pop.style.bottom=(window.innerHeight-r.top+GAP)+'px';
    pop.style.maxHeight=Math.max(200,Math.min(560,acima))+'px';
  }
  pop.style.left=Math.max(MARG,Math.min(r.left,window.innerWidth-LARG-MARG))+'px';
  pop.innerHTML='<button type="button" class="pop-close" title="Fechar" onclick="kbFecharLead()">✕</button><div class="cx-empty">Carregando…</div>';
  document.body.appendChild(pop);
  _leadPop=pop;
  setTimeout(function(){document.addEventListener('click',_leadPopFora,true);document.addEventListener('keydown',_leadPopEsc,true);
    window.addEventListener('scroll',_leadPopRolou,true);},0);
  fetch('/painel/prospeccao/'+id+'/resumo').then(function(r){return r.json();}).then(function(d){
    if(_leadPop!==pop)return;
    if(!d.ok){pop.innerHTML='<div class="cx-empty">Não consegui abrir.</div>';return;}
    pop._d=d;
    pop.innerHTML=kbLeadHtml(d,id);
  }).catch(function(){if(_leadPop===pop)pop.innerHTML='<div class="cx-empty">Falha de rede.</div>';});
}
function kbLeadHtml(d,id){
  var h='<button type="button" class="pop-close" title="Fechar" onclick="kbFecharLead()">✕</button>'
    +'<div class="lp-h"><div class="top">'
    +'<span class="tdot" style="width:12px;height:12px;background:'+cxEscK(d.temp_cor||'#7a7a7a')+'"></span>'
    +'<h3>'+cxEscK(d.empresa||'Lead')+'</h3>';
  if(d.temperatura)h+='<span class="tpill" style="background:'+cxEscK(d.temp_pill[0])+';color:'+cxEscK(d.temp_pill[1])+'">'+cxEscK(d.temperatura)+'</span>';
  h+='</div>';
  var sub=[d.segmento,(d.cidade?(d.cidade+(d.uf?('/'+d.uf):'')):'')].filter(Boolean).join(' · ');
  if(d.vendedor_nome)sub+=(sub?' · ':'')+'👤 '+d.vendedor_nome;
  if(sub)h+='<div class="sub">'+cxEscK(sub)+'</div>';
  if(d.canais_contato&&d.canais_contato.length){
    h+='<div class="lp-canais">';
    d.canais_contato.forEach(function(ch){h+='<span class="lp-canal">'+cxEscK(ch.ic)+' '+cxEscK(ch.label)+(ch.respondeu?' ✓':'')+'</span>';});
    h+='</div>';
  }
  h+='</div><div class="lp-acoes">';
  if(d.tel_link)h+='<a class="lp-ab" href="'+cxEscK(d.tel_link)+'">📞 Ligar</a>';
  if(d.zap_link)h+='<a class="lp-ab" href="'+cxEscK(d.zap_link)+'" target="_blank" rel="noopener">💬 WhatsApp</a>';
  if(d.insta_url)h+='<a class="lp-ab" href="'+cxEscK(d.insta_url)+'" target="_blank" rel="noopener">📷 Instagram</a>';
  if(d.maps_url)h+='<a class="lp-ab" href="'+cxEscK(d.maps_url)+'" target="_blank" rel="noopener">🗺️ Mapa</a>';
  h+='<div class="lp-status"><select onchange="kbLeadStatus(this,'+id+')" data-prev="'+cxEscK(d.status||'')+'">';
  (window._KB_STATUS||[]).forEach(function(s){h+='<option value="'+cxEscK(s[0])+'"'+(s[0]===d.status?' selected':'')+'>'+cxEscK(s[1])+'</option>';});
  h+='</select></div></div><div class="lp-body">'
    +'<div id="lp-view">'+kbLeadDadosHtml(d,id)+kbLeadHistHtml(d)+'</div>'
    +'<div id="lp-edit" style="display:none">'+kbLeadEditHtml(d,id)+'</div>'
    +'</div>'
    +'<a class="lp-mais" target="_blank" href="/painel/prospeccao/'+id+'">Ver ficha completa ↗</a>';
  return h;
}
// "Dados" no resumo do balão: os mesmos campos que a seção "Dados" da ficha
// completa mostra, MENOS o que é enriquecimento automático (sócio, regime,
// porte, Receita) — aquilo não é algo que se corrige rápido, fica só na ficha.
function kbLeadDadosHtml(d,id){
  var h='<div class="lp-sh"><b>Dados</b><button type="button" class="lp-edit-btn" onclick="kbLeadEditar()">✎ Editar</button></div><div class="lp-grid">';
  if(d.evento_fmt||d.evento_pista){
    var org=d.evento_origem==='conversa'?'💬 lido da conversa':d.evento_origem==='agente'?'🤖 lido pelo agente':d.evento_origem==='confirmado'?'✓ confirmado':d.evento_origem==='orcamento'?'do orçamento':'';
    h+='<div class="full"><div class="k">Evento'+(org?(' · '+org+(d.evento_lido_fmt?(' em '+cxEscK(d.evento_lido_fmt)):'')):'')+'</div>'
      +'<div class="v">'+(d.evento_fmt?cxEscK(d.evento_fmt):'<span style="color:#e0b45f">sem data</span>')+'</div>'
      +(d.evento_trecho?('<div class="lp-trecho">“'+cxEscK(d.evento_trecho)+'”</div>'):'')
      +(d.evento_pista?('<div class="lp-pista">💬 '+cxEscK(d.evento_pista)+'</div>'):'')
      +((d.evento_origem==='conversa'||d.evento_origem==='agente')?('<div class="lp-evbts"><button type="button" class="pbtn" onclick="kbLeadConfirmarEvento('+id+')">✓ Confirmar</button><button type="button" class="pbtn ghost" onclick="kbLeadEditar()">Corrigir</button></div>'):'')
      +'</div>';
  }
  if(d.contato)h+='<div><div class="k">Contato</div><div class="v">'+cxEscK(d.contato)+(d.cargo?(' · '+cxEscK(d.cargo)):'')+'</div></div>';
  if(d.doc_fmt)h+='<div><div class="k">'+cxEscK(d.doc_rot||'Documento')+'</div><div class="v">'+cxEscK(d.doc_fmt)+'</div></div>';
  if(d.telefone)h+='<div><div class="k">Telefone</div><div class="v">'+cxEscK(d.telefone)+'</div></div>';
  if(d.whatsapp)h+='<div><div class="k">WhatsApp</div><div class="v">'+cxEscK(d.whatsapp)+'</div></div>';
  if(d.email)h+='<div><div class="k">E-mail</div><div class="v">'+cxEscK(d.email)+'</div></div>';
  if(d.instagram)h+='<div><div class="k">Instagram</div><div class="v">'+cxEscK(d.instagram)+'</div></div>';
  if(d.site_url)h+='<div><div class="k">Site</div><div class="v"><a href="'+cxEscK(d.site_url)+'" target="_blank" rel="noopener" style="color:var(--verde-claro)">'+cxEscK(d.site_dominio||d.site_url)+'</a></div></div>';
  if(d.valor_fmt)h+='<div><div class="k">Valor estimado</div><div class="v">'+cxEscK(d.valor_fmt)+'</div></div>';
  if(d.obs)h+='<div class="full"><div class="k">Observação</div><div class="v">'+cxEscK(d.obs)+'</div></div>';
  return h+'</div>';
}
function kbLeadHistHtml(d){
  if(!d.atividades||!d.atividades.length)return '';
  var h='<div class="lp-sec2"><div class="lp-sh"><b>Histórico</b></div>';
  d.atividades.forEach(function(a){
    h+='<div class="lp-ativ"><span class="dot2" style="background:'+cxEscK(a.cor||'#7a7a7a')+'"></span><div>'
      +'<div>'+cxEscK(a.tipo_rot||'')+(a.resultado_rot?' — '+cxEscK(a.resultado_rot):'')+(a.descricao?': '+cxEscK(a.descricao):'')+'</div>'
      +'<div class="qd">'+cxEscK(a.quando||'')+'</div></div></div>';
  });
  return h+'</div>';
}
function _lpVal(v){return cxEscK(v==null?'':v);}
// OS CAMPOS DO EVENTO são declarados pela TELA (`window._KB_EVENTO`, ver o
// docstring deste módulo) e não existem aqui — nem o rótulo, nem o exemplo, nem o
// nome do campo. É o portão do §6: a conta que não vende data não recebe a palavra
// na página, e não só deixa de ver o campo. Comentário de JS viaja inteiro pro
// navegador, então nem aqui a palavra do nicho pode estar escrita.
function _lpCamposEvento(d){
  var E=window._KB_EVENTO; if(!E||!E.campos) return '';
  var h='';
  E.campos.forEach(function(c){
    h+='<div><label>'+cxEscK(c.rot)+'</label><input id="lp-ed-'+cxEscK(c.id)+'" '+(c.attr||'')
      +' value="'+_lpVal(d[c.de])+'"></div>';
  });
  return h;
}
function kbLeadEditHtml(d,id){
  return '<div class="lp-sh"><b>Editando os dados</b></div><div class="lp-ed-grid">'
    +'<div><label>Contato</label><input id="lp-ed-contato" value="'+_lpVal(d.contato)+'"></div>'
    +'<div><label>Cargo</label><input id="lp-ed-cargo" value="'+_lpVal(d.cargo)+'"></div>'
    +'<div><label>Telefone</label><input id="lp-ed-telefone" value="'+_lpVal(d.telefone)+'"></div>'
    +'<div><label>WhatsApp</label><input id="lp-ed-whatsapp" value="'+_lpVal(d.whatsapp)+'"></div>'
    +'<div><label>E-mail</label><input id="lp-ed-email" value="'+_lpVal(d.email)+'"></div>'
    +'<div><label>Instagram</label><input id="lp-ed-instagram" value="'+_lpVal(d.instagram)+'"></div>'
    +'<div class="full"><label>Site</label><input id="lp-ed-site" value="'+_lpVal(d.site_url)+'"></div>'
    // OS TRÊS CAMPOS DO EVENTO só pra quem vende data (CLAUDE.md §6, e ver o
    // docstring deste módulo). Os RÓTULOS vêm da tela, em `window._KB_EVENTO`, e
    // não estão escritos aqui — nem em comentário: comentário de JS viaja inteiro
    // pro navegador, e o portão do §6 é sobre a palavra NÃO CHEGAR na conta que
    // não é daquele nicho, não sobre ela ficar escondida atrás de um `if`.
    +_lpCamposEvento(d)
    +'<div class="full"><label>Valor estimado</label><input id="lp-ed-valor" value="'+_lpVal(d.valor_edit)+'"></div>'
    +'<div class="full"><label>Observação</label><textarea id="lp-ed-obs">'+_lpVal(d.obs)+'</textarea></div>'
    +'</div><div class="lp-ed-acoes">'
    +'<button type="button" class="lp-ed-salvar" onclick="kbLeadSalvar('+id+')">Salvar</button>'
    +'<button type="button" class="lp-ed-cancelar" onclick="kbLeadCancelarEdicao()">Cancelar</button></div>';
}
function kbLeadEditar(){
  var v=document.getElementById('lp-view'), e=document.getElementById('lp-edit');
  if(v)v.style.display='none';
  if(e)e.style.display='block';
}
function kbLeadCancelarEdicao(){
  var v=document.getElementById('lp-view'), e=document.getElementById('lp-edit');
  if(e)e.style.display='none';
  if(v)v.style.display='block';
}
// Rota PRÓPRIA (não a /editar da ficha completa — ver comentário na rota):
// só os campos que o balão mostra. Depois de salvar, busca o /resumo de novo
// e redesenha (mesma função que abriu o balão), voltando pro modo leitura já
// com o dado novo.
function kbLeadSalvar(id){
  var btn=_leadPop&&_leadPop.querySelector('.lp-ed-salvar');
  if(btn){btn.disabled=true;btn.textContent='Salvando…';}
  var body=new URLSearchParams();
  // campo do formulário → id do input no balão (os três do evento: migração 197)
  [['contato','contato'],['cargo','cargo'],['telefone','telefone'],['whatsapp','whatsapp'],
   ['email','email'],['instagram','instagram'],['site_url','site'],['valor','valor'],['obs','obs']
  ].concat(((window._KB_EVENTO||{}).campos||[]).map(function(c){return [c.k,c.id];})
  ).forEach(function(p){var el=document.getElementById('lp-ed-'+p[1]);if(el)body.append(p[0],el.value);});
  fetch('/painel/prospeccao/'+id+'/editar-rapido',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:body})
    .then(function(r){return r.json();}).then(function(d){
      if(!_leadPop)return;
      if(!d.ok){if(btn){btn.disabled=false;btn.textContent='Salvar';}alert('Não consegui salvar.');return;}
      return fetch('/painel/prospeccao/'+id+'/resumo').then(function(r){return r.json();}).then(function(d2){
        if(!_leadPop||!d2.ok)return;
        _leadPop._d=d2;
        _leadPop.innerHTML=kbLeadHtml(d2,id);
      });
    }).catch(function(){if(btn){btn.disabled=false;btn.textContent='Salvar';}alert('Falha de rede.');});
}
// A mesma rota da ficha completa (fichaStatus lá dentro) — só troca a situação
// e, se deu certo, move o card pra coluna nova no board por trás (mesma
// varredura de contagem do drag-and-drop) e fecha o balão: o resultado
// (card na coluna nova) já fica visível sem o balão flutuando desalinhado.
function kbLeadStatus(sel,id){
  var novo=sel.value, prev=sel.getAttribute('data-prev')||'';
  var body=new URLSearchParams();body.append('status',novo);
  fetch('/painel/prospeccao/'+id+'/status',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:body})
    .then(function(r){return r.json();}).then(function(d){
      if(!d.ok){alert('Não consegui mudar a situação.');sel.value=prev;return;}
      // O QUE ACONTECE DEPOIS é de cada tela, e por isso é um gancho e não um
      // `if` por tela aqui dentro: no funil o card anda pra coluna nova sem
      // recarregar nada; no Follow-up a troca pode TIRAR o lead da lista (ganho e
      // perdido saem do funil) e mexe nos quatro números do topo, então a resposta
      // honesta é recarregar. Tela sem gancho recarrega — o lado seguro de errar é
      // mostrar o estado novo, nunca o antigo.
      if(typeof window.kbDepoisDoStatus==='function'){window.kbDepoisDoStatus(d,id,novo);kbFecharLead();return;}
      location.reload();
    }).catch(function(){alert('Falha de rede.');sel.value=prev;});
}"""


#: Os rótulos dos três campos do evento, pra tela emitir DENTRO do seu próprio
#: `{% if %}` de nicho (§6). Ficam aqui, e não escritos nos dois templates, pra não
#: nascerem duas versões da mesma palavra — mas ficam FORA do `JS` de propósito:
#: assim a conta que não vende data não recebe nem a palavra na página, que é o que
#: o teste do §6 confere (`test_o_vocabulario_de_festa_so_aparece_pra_quem_vende_festa`).
EVENTO_JS = (
    'window._KB_EVENTO={"campos":['
    '{"k":"evento_tipo","id":"evtipo","de":"evento_tipo","rot":"Tipo do evento",'
    '"attr":"placeholder=\'Casamento, anivers\\u00e1rio\\u2026\'"},'
    '{"k":"evento_em","id":"evdata","de":"evento_iso","rot":"Data do evento",'
    '"attr":"type=\'date\'"},'
    '{"k":"evento_convidados","id":"evconv","de":"evento_convidados","rot":"Convidados",'
    '"attr":"inputmode=\'numeric\'"}]};')

def registrar_jinja(env) -> None:
    """Deixa `janela_css` e `janela_js` disponíveis em qualquer template do env.

    `Markup` pro bloco sair cru mesmo com autoescape ligado: o JS tem aspas e
    `&&` que viram entidade e quebram o script sem avisar — a mesma armadilha
    documentada em `balao_conversa.registrar_jinja`.
    """
    env.globals["janela_css"] = Markup(CSS)
    env.globals["janela_js"] = Markup(JS)
    env.globals["janela_evento_js"] = Markup(EVENTO_JS)
