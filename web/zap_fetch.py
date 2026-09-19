"""Um caminho só pra o painel falar com o servidor — e saber o que deu errado.

O PEDIDO (dono, 19/09/2026): "vamos fazer o zapFetch agora", depois de bater num
alerta "Falha de rede." ao mudar a situação de um lead e mandar olhar "em todos
os lugares onde puxa a mesma". Diagnóstico e desenho em
`docs/mockups/falha_de_rede.html`.

O QUE A FRASE ESCONDIA. Todo botão do painel faz

    fetch(url, …).then(r => r.json()).then(d => …).catch(() => alert('Falha de rede.'))

e esse `.catch` dispara pra CINCO coisas diferentes: a rede caiu, o Zaq estava
subindo uma versão (502/503/504 do proxy do Render), a sessão expirou, o servidor
deu 500, ou a aba está com o JavaScript de antes do deploy. Em quatro delas a
palavra "rede" é falsa, e em nenhuma ficava registro de coisa alguma — por isso
"o que houve às 19:33 do dia 15?" não teve resposta.

Eram 119 chamadas assim, 68 alertas com essa frase, 14 telas. E UM lugar que já
fazia certo — a faixa de "tem versão nova", em web/portal.py:
`return r.ok ? r.json() : null`. Este módulo é aquela linha virando regra.

COMO SE USA:

    zapFetch(url, {method:'POST', body: …}).then(function(d){
      if(!d) return;              // já avisei a pessoa; não há o que fazer aqui
      if(!d.ok) { … }             // o SERVIDOR respondeu e disse não — é do chamador
      …
    });

SEM `.catch`. A promessa NUNCA é rejeitada: ou entrega o corpo, ou entrega `null`
depois de ter mostrado o aviso certo. É por isso que `if(!d) return;` substitui o
`.catch` em vez de conviver com ele — dois lugares tratando a mesma falha é como
se volta a ter cinco causas com um recado só.

O QUE É FALHA E O QUE NÃO É — a distinção que faz o resto funcionar:

* `{ok:false, erro:'motivo_obrigatorio', motivos:[…]}` com HTTP 400 NÃO é falha.
  O servidor entendeu, decidiu e respondeu; quem sabe o que fazer com isso é a
  tela. Por isso, se o corpo é JSON, ele VOLTA, seja qual for o status — foi
  exatamente assim que a folha "por que perdeu" nasceu, e engolir o 400 aqui
  mataria ela de novo.
* corpo que não é JSON, ou `fetch` que estourou, É falha: ninguém do outro lado
  respondeu uma coisa que a tela saiba ler.

A ÚNICA EXCEÇÃO É O 401: o corpo é JSON (`{ok:false, erro:'login'}`), mas devolver
isso faria a tela dizer "não consegui salvar (login)" — técnico e inútil. Sessão
expirada tem um caminho só, e ele é um botão: entrar de novo.

QUEM DECIDE PELO STATUS HTTP pede `comStatus: true` e recebe `{ok, status, d}` no
lugar do corpo pelado. É o caso da aba de Serviços: as rotas dela sinalizam falha
com `{erro:…}` + 4xx e sucesso com o resultado puro, **sem `ok` dentro do corpo**.
Sem esse modo, migrar aquelas chamadas faria `res.ok` virar um campo inexistente —
e todo salvamento bem-sucedido passaria a dizer "não consegui salvar", na tela do
dinheiro. O padrão continua sendo o corpo pelado: é o que 152 chamadas usam.

TENTA DE NOVO SOZINHO no 502/503/504 e na queda de conexão, duas vezes (2s e 5s).
Essa é a janela do deploy: o Render derruba e sobe o serviço em segundos, e na
maioria das vezes a pessoa não vê aviso nenhum — o toque só demora um pouco mais.
Só em método seguro de repetir, ver `_REPETIVEL`.

AVISO NO CANTO, NÃO `alert()`. O alerta do navegador para tudo até alguém clicar
OK, não cabe botão de "tentar de novo" e no celular cobre a tela inteira.
"""
from markupsafe import Markup

CSS = """/* O AVISO — canto de baixo, por cima de tudo, sem travar a tela.
   z-index acima dos popovers (90) porque ele fala SOBRE o que acontece dentro
   deles: um aviso atrás da janela do lead é um aviso que não existe. */
.zap-avisos{position:fixed;left:50%;transform:translateX(-50%);bottom:1rem;z-index:200;
  display:flex;flex-direction:column;gap:.4rem;align-items:center;pointer-events:none;
  width:min(30rem,calc(100vw - 1.5rem))}
.zap-aviso{pointer-events:auto;display:flex;align-items:center;gap:.6rem;max-width:100%;
  background:var(--card,#14181a);border:1px solid var(--borda,#2a2f31);
  border-left:3px solid var(--txt-mut,#8a9a91);border-radius:10px;
  padding:.6rem .85rem;font-size:.86rem;line-height:1.4;color:var(--txt,#eaf2ed);
  box-shadow:0 10px 26px rgba(0,0,0,.45);animation:zap-sobe .18s ease-out}
@keyframes zap-sobe{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
.zap-aviso .ic{flex:none;font-size:1rem;line-height:1}
.zap-aviso .tx{flex:1;min-width:0;overflow-wrap:anywhere}
.zap-aviso .tx small{display:block;color:var(--txt-mut,#8a9a91);font-size:.74rem;margin-top:.1rem}
/* `width:auto;margin:0` vencem o `button{width:100%;margin-top:1.4rem}` global —
   a mesma armadilha que já esticou o ✕ de fechar e o ✕ de excluir o card. */
.zap-aviso .bt{flex:none;width:auto;margin:0;padding:.25rem .6rem;border-radius:7px;
  background:none;border:1px solid var(--borda,#2a2f31);color:var(--verde-claro,#46f58a);
  font:600 .78rem inherit;font-family:inherit;cursor:pointer;white-space:nowrap}
.zap-aviso .bt:hover{border-color:var(--verde,#25d366)}
.zap-aviso .x{flex:none;width:auto;margin:0;padding:0 .2rem;background:none;border:0;
  color:var(--txt-mut,#8a9a91);cursor:pointer;font-size:.9rem;line-height:1}
.zap-aviso.mal{border-left-color:var(--coral,#e0574f)}
.zap-aviso.esp{border-left-color:var(--ambar,#e0a32e)}
.zap-aviso.ok{border-left-color:var(--verde,#25d366)}"""

JS = r"""// Guardado contra dupla inclusão: este bloco vive no template BASE, e uma tela
// que um dia o traga de novo por conta própria não pode redefinir o que já está
// no ar no meio de um toque.
if(!window.zapFetch){
(function(){
  // Métodos que dá pra repetir sem risco de fazer a coisa duas vezes. POST fica
  // de fora: "criar o orçamento" repetido cria dois. A retentativa aqui é só
  // pra quando a resposta NÃO CHEGOU — e mesmo assim o servidor pode ter
  // processado, então só onde repetir é inofensivo.
  var _REPETIVEL = {GET:1, HEAD:1};
  var _ESPERAS = [2000, 5000];          // 502/503/504: a janela do deploy
  var _PASSAGEIRO = {502:1, 503:1, 504:1};

  function _caixa(){
    var c = document.querySelector('.zap-avisos');
    if(!c){ c = document.createElement('div'); c.className = 'zap-avisos';
            c.setAttribute('aria-live','polite'); document.body.appendChild(c); }
    return c;
  }
  // texto puro sempre: a mensagem carrega pedaço de resposta do servidor, e
  // resposta de servidor não entra no DOM como HTML.
  function _txt(el, s){ el.textContent = (s == null ? '' : String(s)); }

  // o aviso. `op` = {tipo:'mal'|'esp'|'ok', detalhe, acao:{texto, fn}, some:ms}
  function zapAviso(texto, op){
    op = op || {};
    var cx = _caixa();
    var el = document.createElement('div');
    el.className = 'zap-aviso' + (op.tipo ? ' ' + op.tipo : '');
    var ic = document.createElement('span'); ic.className = 'ic';
    _txt(ic, op.icone || (op.tipo === 'ok' ? '✓' : op.tipo === 'esp' ? '🔄' : '⚠️'));
    var tx = document.createElement('span'); tx.className = 'tx';
    var b = document.createElement('b'); b.style.fontWeight = '500'; _txt(b, texto);
    tx.appendChild(b);
    if(op.detalhe){ var s = document.createElement('small'); _txt(s, op.detalhe); tx.appendChild(s); }
    el.appendChild(ic); el.appendChild(tx);
    if(op.acao){
      var bt = document.createElement('button');
      bt.type = 'button'; bt.className = 'bt'; _txt(bt, op.acao.texto);
      bt.onclick = function(){ el.remove(); op.acao.fn(); };
      el.appendChild(bt);
    }
    var x = document.createElement('button');
    x.type = 'button'; x.className = 'x'; x.title = 'Fechar'; _txt(x, '✕');
    x.onclick = function(){ el.remove(); };
    el.appendChild(x);
    cx.appendChild(el);
    // SÓ O QUE DEU CERTO SOME SOZINHO. Erro fica até a pessoa mandar embora:
    // um recado que some antes de ser lido é o mesmo que não ter recado.
    var some = op.some != null ? op.some : (op.tipo === 'ok' || op.tipo === 'esp' ? 4000 : 0);
    if(some) setTimeout(function(){ el.remove(); }, some);
    return el;
  }

  // Manda o ocorrido pro servidor guardar. FOGO E ESQUECE, com `fetch` cru de
  // propósito: se o registro do erro usasse zapFetch, um erro no registro
  // tentaria se registrar, e assim por diante.
  function _registrar(dados){
    try{
      fetch('/painel/erro-cliente', {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify(dados), keepalive:true}).catch(function(){});
    }catch(e){}
  }

  function _versaoDaAba(){ return (window.ZAQ_VERSAO || ''); }

  function zapFetch(url, op){
    op = op || {};
    var metodo = (op.method || 'GET').toUpperCase();
    var tentativa = 0;

    function _vai(){
      return fetch(url, op).then(function(r){
        // A ABA VELHA. O servidor carimba a versão em toda resposta; se não é a
        // que esta aba carregou, o deploy aconteceu com ela aberta e o JS daqui
        // pode não entender mais o que vem. Vale ANTES de olhar o corpo: um
        // corpo novo lido por um JS velho é o pior dos dois mundos.
        var vs = r.headers.get('X-Zaq-Versao');
        var minha = _versaoDaAba();
        if(vs && minha && vs !== minha){
          zapAviso('Esta aba está desatualizada.', {tipo:'esp', icone:'🧭',
            detalhe:'O Zaq foi atualizado enquanto ela estava aberta.',
            acao:{texto:'Recarregar', fn:function(){ location.reload(); }}, some:0});
          return null;
        }
        if(r.status === 401){
          zapAviso('Sua sessão expirou.', {tipo:'mal', icone:'🔑',
            detalhe:'Entre de novo pra continuar de onde parou.',
            acao:{texto:'Entrar de novo', fn:function(){ location.href = '/login'; }}, some:0});
          return null;
        }
        if(_PASSAGEIRO[r.status] && tentativa < _ESPERAS.length
           && (_REPETIVEL[metodo] || op.repetivel)){
          return _dePois();
        }
        return r.text().then(function(corpo){
          var d = null;
          try{ d = JSON.parse(corpo); }catch(e){}
          // CORPO QUE É JSON VOLTA, seja qual for o status: `{ok:false,
          // erro:'motivo_obrigatorio', motivos:[…]}` vem com 400 e é justamente
          // o que a folha "por que perdeu" precisa ler.
          if(d !== null && typeof d === 'object'){
            // `comStatus` (20/09/2026): entrega {ok, status, d} em vez do corpo
            // pelado. É pra quem decide pelo STATUS HTTP e não por um campo do
            // corpo — o caso da aba de Serviços, cujas rotas sinalizam falha com
            // `{erro:…}` + 4xx e sucesso com o resultado puro, SEM `ok` dentro.
            // Sem este modo, migrar aquelas chamadas faria `res.ok` virar um
            // campo que não existe: todo salvamento BEM-SUCEDIDO passaria a
            // dizer "não consegui salvar", e na tela do dinheiro.
            return op.comStatus ? {ok: r.ok, status: r.status, d: d} : d;
          }
          return _semResposta(r, corpo);
        });
      }, function(){
        // o `fetch` nem saiu: ou não há rede, ou o servidor não atendeu.
        if(tentativa < _ESPERAS.length && (_REPETIVEL[metodo] || op.repetivel)) return _dePois();
        if(navigator.onLine === false){
          zapAviso('Você está sem internet.', {tipo:'mal', icone:'📡',
            detalhe:'Nada foi perdido — tente de novo quando voltar.', some:0});
          return null;
        }
        zapAviso('Não consegui falar com o Zaq.', {tipo:'mal', icone:'📡',
          detalhe:'Pode ser a sua conexão ou uma atualização em andamento.', some:0});
        return null;
      });
    }

    function _dePois(){
      var espera = _ESPERAS[tentativa++];
      if(tentativa === 1){
        zapAviso('Estamos atualizando o Zaq. Tentando de novo…',
                 {tipo:'esp', icone:'🔄', some:espera + 1500});
      }
      return new Promise(function(ok){ setTimeout(function(){ ok(_vai()); }, espera); });
    }

    function _semResposta(r, corpo){
      var trecho = (corpo || '').slice(0, 300);
      _registrar({url:String(url), metodo:metodo, status:r.status,
                  corpo:trecho, versao_aba:_versaoDaAba()});
      if(r.status >= 500){
        zapAviso('Deu um erro do nosso lado.', {tipo:'mal',
          detalhe:'O ocorrido ficou registrado. Tente de novo em instantes.', some:0});
      }else{
        zapAviso('Não consegui completar essa ação.', {tipo:'mal',
          detalhe:'A resposta veio num formato que esta tela não entende.', some:0});
      }
      return null;
    }

    return _vai();
  }

  window.zapAviso = zapAviso;
  window.zapFetch = zapFetch;
})();
}"""


def registrar_jinja(env) -> None:
    """Deixa `zap_css` e `zap_js` disponíveis em qualquer template do env.

    Os dois entram no template BASE, e não tela a tela, de propósito: em 19/09
    duas telas quebraram no mesmo dia por usarem algo que só existia porque OUTRA
    tela tinha carregado o módulo (o `kbFecharChat`, que deu botão morto, e o
    `.pop-close`, que deu faixa verde). Infraestrutura que toda tela usa não pode
    depender de cada tela lembrar de trazê-la.

    `Markup` pra o bloco sair cru mesmo com autoescape ligado: o JS tem aspas e
    `&&` que viram entidade e quebram o script sem avisar.
    """
    env.globals["zap_css"] = Markup(CSS)
    env.globals["zap_js"] = Markup(JS)
