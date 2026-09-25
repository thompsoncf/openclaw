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
2. `var _KB_STATUS = {{ status|tojson }}` — o que `lista_de_status()` aqui
   embaixo devolve a partir das etapas DA CONTA: `[{c, r, sai, fim}, ...]`. É ela
   que enche o seletor de situação. Sem ela o seletor sai vazio e a janela vira só
   leitura; por isso a leitura aqui é `window._KB_STATUS` e não `_KB_STATUS` cru,
   que estouraria um ReferenceError e mataria o clique inteiro por causa de um
   seletor.

   AS DUAS TELAS PASSAM A LISTA INTEIRA (17/09/2026). Até esse dia o funil montava
   `_KB_STATUS` a partir da lista já filtrada pelo quadro — a mesma variável que
   decide as COLUNAS —, e o Follow-up passava a lista completa. Resultado: a MESMA
   janela, no MESMO lead, oferecia 6 situações abrindo pelo quadro e 9 abrindo pelo
   Follow-up. Na Prime as três que sumiam eram "Agendado Visita", "Evento A
   Realizar" e "Evento Realizado" — esta última é o `ganho` da conta, então quem
   trabalhava pelo quadro NÃO TINHA COMO MARCAR A VENDA. Ver o comentário de
   `etapas_todas` em `web/painel_prospeccao.py`.

O GANCHO `window.kbDepoisDoStatus(d, id, novo)`, opcional: o que a tela faz depois
de a situação mudar. O funil move o card pra coluna nova sem recarregar; quem não
define o gancho recarrega a página, que é o certo pra uma lista que pode ter
perdido o lead (ganho e perdido saem do funil).

O QUE NÃO ESTÁ AQUI, de propósito: `kbPerguntarData` e `kbLerConversas` continuam
no funil — o primeiro depende de uma variável que só aquela tela tem, e o segundo
é um botão do cabeçalho do quadro, não da janela.
"""
from markupsafe import Markup

#: As duas etapas que NÃO são passo de funil, e sim desfecho. O rótulo é livre
#: (a Prime chama `ganho` de "Evento Realizado"), a chave não — e é pela chave
#: que o Raio-X conta venda e perda. Por isso a separação é por chave.
DESFECHOS = ("ganho", "perdido")


def lista_de_status(etapas) -> list[dict]:
    """As situações que o vendedor pode escolher, a partir das etapas da conta.

    Recebe o que `_etapas()` devolve (dicts com chave/rotulo/sai_do_quadro) e
    entrega o que o JS consome:

        {"c": chave, "r": rótulo, "sai": sai do quadro?, "fim": "" | "ganho" | "perdido"}

    Nomes curtos de propósito: isto vai inteiro pro HTML de toda tela que abre a
    janela, uma vez por página.

    A LISTA VEM INTEIRA. Etapa marcada "sai do quadro" continua aqui — o que ela
    faz é sumir com a COLUNA, não com a escolha; a janela a mostra tracejada e
    avisa que o card vai sair dali. Filtrar aqui é o defeito que esta função
    nasceu pra impedir de voltar.
    """
    saida = []
    for e in etapas:
        chave = e["chave"]
        saida.append({"c": chave, "r": e["rotulo"],
                      "sai": bool(e.get("sai_do_quadro")),
                      "fim": chave if chave in DESFECHOS else ""})
    return saida


def parado_texto(desde, agora) -> str:
    """"parado há 20 dias" — o tempo na situação atual, pro cabeçalho do bloco.

    Devolve "" pra menos de um dia: "parado há 0 dias" num lead que chegou hoje é
    cobrança errada, e o vendedor aprende a ignorar o rótulo inteiro.
    """
    if not desde:
        return ""
    dias = (agora - desde).days
    if dias < 1:
        return ""
    return "parado há 1 dia" if dias == 1 else f"parado há {dias} dias"


from web.balao_conversa import COMUM as _COMUM_POPOVER

#: `_COMUM_POPOVER` (o ✕ de fechar, o vazio e o balão de mensagem) vem do módulo
#: do balão de conversa porque é DOS DOIS popovers, e uma cópia aqui divergiria.
#: Entrou em 19/09/2026: a Comunicação carrega só esta janela, e sem o
#: `.pop-close` o ✕ virava uma barra verde da largura inteira — o
#: `button{width:100%}` global vencendo por falta de quem o vencesse. Ver o
#: comentário do `COMUM` lá, que guarda o caso.
CSS = _COMUM_POPOVER + """/* o balão do LEAD — resumo pra decidir a próxima ação (contato, valor, situação,
   últimas atividades). Mesma engenharia do balão de chat: nasce fixed, medido
   do próprio card, sem carregar a ficha inteira num iframe. Edição de cadastro,
   IA, decisor e orçamento continuam só na ficha completa (link no rodapé). */
/* A JANELA INTEIRA À VISTA (25/09/2026). Até aqui ela abria no espaço que sobrava
   embaixo do botão, com a altura desse espaço — e o topo (nome, Ligar/WhatsApp e os
   dois grupos de situação) não encolhe: com ~300px sobrando, `overflow:hidden`
   cortava "Encerrar" e o histórico, sem barra pra rolar. O lugar agora é decidido
   DEPOIS de medir o conteúdo (`_leadPopPosiciona`), e `overflow-y:auto` aqui é a
   última rede: tela baixa demais pra janela inteira rola a janela, nunca corta. */
.leadpop{position:fixed;z-index:90;width:378px;max-width:calc(100vw - 16px);max-height:calc(100vh - 16px);
  background:var(--card);border:1px solid var(--borda);border-radius:14px;overflow-x:hidden;overflow-y:auto;
  box-shadow:0 18px 46px rgba(0,0,0,.5);display:flex;flex-direction:column}
/* o botão de suporte (web/portal.py, z-index 9999) ficava POR CIMA do canto da
   janela — justamente onde ela abre, ancorada no "Abrir ficha" da direita */
body.lp-aberta .wa-suporte{visibility:hidden}
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
/* O BLOCO DA SITUAÇÃO (17/09/2026). Era um <select> de .76rem encostado no canto
   direito da fileira de ações: no celular, um toque abre a roleta do sistema, outro
   escolhe, outro confirma — três toques e uma lista tampando a tela pra dizer
   "este virou proposta". O app já resolvia isso com botão desde sempre; aqui só
   chegou agora.

   `margin:0` e `width:auto` em todo botão: o `button{width:100%;margin-top:1.4rem}`
   global vaza pra dentro da janela — é a mesma armadilha que já pegou o ✕ de
   excluir, o "✎ Editar" e os botões da folha de perda. */
.lp-sit{padding:.6rem .85rem;border-bottom:1px solid var(--borda);flex:none}
.lp-sit-h{display:flex;align-items:baseline;gap:.5rem;margin-bottom:.4rem}
.lp-sit-h b{font-size:.68rem;text-transform:uppercase;letter-spacing:.06em;color:var(--txt-mut)}
.lp-sit-h em{font-style:normal;font-size:.72rem;color:#e0b45f}
.lp-sit-h.fim{margin-top:.55rem;padding-top:.55rem;border-top:1px solid var(--borda)}
.lp-chips{display:flex;gap:.35rem;flex-wrap:wrap}
.lp-chip{margin:0;width:auto;font:500 .76rem inherit;font-family:inherit;padding:.3rem .65rem;border-radius:999px;
  border:1px solid var(--borda);background:var(--bg);color:var(--txt-mut);cursor:pointer;white-space:nowrap}
.lp-chip:hover:not(:disabled){border-color:var(--verde);color:var(--txt)}
.lp-chip:disabled{opacity:.5;cursor:default}
.lp-chip.on{border-color:var(--verde);background:rgba(37,211,102,.12);color:var(--verde-claro);font-weight:600;cursor:default}
/* o PASSO SEGUINTE — o que a pessoa quase sempre veio fazer. Sem isto ela relê a
   fileira inteira pra achar o único chip que ia usar. */
.lp-chip.prox{border-color:var(--neon-borda,#1E4A3A);background:var(--neon-fundo);color:var(--verde-claro);
  box-shadow:0 0 0 1px rgba(62,224,166,.18)}
.lp-chip.prox::after{content:" →";opacity:.65}
/* tracejado = ao entrar aqui o CARD SAI DO QUADRO. A escolha continua existindo
   (é o conserto de 17/09); o aviso é pra ninguém achar que o lead sumiu. */
.lp-chip.sai{border-style:dashed}
.lp-chip.fim.ganho{border-color:var(--verde);color:var(--verde-claro)}
.lp-chip.fim.perdido{border-color:#5a2b2b;color:#f0a9a2}
.lp-chip.fim.ganho.on{background:rgba(37,211,102,.14)}
.lp-chip.fim.perdido.on{background:rgba(224,87,79,.14)}
.lp-chip.indo{opacity:.6}
.lp-body{padding:.7rem .85rem;overflow-y:auto;flex:1 1 auto;min-height:6.5rem}
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
/* A FOLHA "por que perdeu" — a lista de seis que o painel não tinha. Centrada e
   por cima de tudo (o arrastar acontece no quadro, sem janela aberta, então ela
   não pode se ancorar em card nenhum). Os `margin:0;width:auto` nos botões vencem
   o `button{width:100%;margin-top:1.4rem}` global, a mesma armadilha que já pegou
   o ✕ de excluir e o "✎ Editar". */
/* O MODO SEGURADO (18/09/2026, docs/mockups/ficha_segurado_janela.html): a mesma
   moldura, com abas Cliente | Apólice e o conteúdo do nicho seguros.
   UMA VOZ SÓ (revisão do mesmo dia, depois de o dono ver a primeira versão: "não
   está harmônico"). Numa coluna de 378px, sete vocabulários visuais — pílula, selo
   em caixa-alta, cabeçalho em caixa-alta, grade rotulada, cartão de borda colorida,
   barra, caixa tracejada — não formam uma voz. Aqui: TRÊS tamanhos de texto
   (.92 / .82 / .72rem), rótulo silencioso sem caixa-alta, cor só onde é estado, e
   a conversa nos MESMOS balões do chat (cx-m) que o produto já usa. */
.lp-h-seg{padding:.8rem 2.3rem .7rem .95rem}
.lp-h-seg .top{align-items:center;gap:.5rem}
.lp-h-seg h3{font-size:.98rem;line-height:1.25;flex:1;min-width:0}
.lp-h-seg .lp-estado{flex:none}
.lp-h-seg .sub{margin-top:.3rem;font-size:.76rem}
.lp-tabs{display:flex;border-bottom:1px solid var(--borda);flex:none;background:var(--card-2)}
.lp-tabs button{flex:1;background:none;border:0;border-bottom:2px solid transparent;padding:.5rem .3rem;margin:0;width:auto;
  color:var(--txt-mut);font-size:.78rem;font-weight:600;cursor:pointer}
.lp-tabs button.on{color:var(--verde-claro);border-bottom-color:var(--verde)}
.lp-body{overflow:auto;padding:.2rem .95rem .9rem;flex:1;min-height:0}
.lp-sec{padding:.75rem 0 .2rem}
.lp-sec+.lp-sec{border-top:1px solid var(--borda)}
.lp-sec .st{display:flex;align-items:baseline;justify-content:space-between;font-size:.72rem;color:var(--txt-mut);margin-bottom:.45rem}
.lp-sec .st b{font-weight:600;color:var(--txt);font-size:.82rem}
.lp-fato{display:grid;grid-template-columns:5.6rem 1fr;gap:.28rem .6rem;font-size:.82rem;line-height:1.45}
.lp-fato .k{color:var(--txt-mut)}
.lp-fato .v{overflow-wrap:anywhere}
.lp-fato .v.mono{font-family:var(--mono);font-size:.76rem}
.lp-estado{display:inline-block;font-size:.72rem;font-weight:600;padding:.12rem .55rem;border-radius:999px;
  background:var(--card-2);border:1px solid var(--borda);color:var(--txt-mut);vertical-align:middle}
.lp-estado.regua{background:var(--ambar-fundo);border-color:var(--ambar-borda);color:#F0DCA6}
.lp-estado.ok{background:var(--neon-fundo);border-color:var(--neon-borda);color:var(--verde-claro)}
.lp-estado.mal{background:var(--coral-fundo);border-color:var(--coral-borda);color:#F0A8A2}
.lp-ap{padding:.55rem 0 .2rem;cursor:pointer;border-radius:8px}
.lp-ap+.lp-ap{border-top:1px dashed var(--borda)}
.lp-ap:hover .t{color:var(--verde-claro)}
.lp-ap .t{display:flex;align-items:center;gap:.45rem;font-weight:600;font-size:.86rem}
.lp-ap .t .lp-estado{margin-left:auto}
.lp-ap .m{color:var(--txt-mut);font-size:.76rem;margin-top:.12rem}
.lp-ap .d{font-size:.8rem;margin-top:.3rem;display:flex;gap:1rem;flex-wrap:wrap}
.lp-ap .d span b{display:block;font-weight:600}
.lp-ap .d span{color:var(--txt-mut);font-size:.7rem}
.lp-regua{margin-top:.5rem}
.lp-regua i{display:block;height:4px;border-radius:2px;background:var(--borda);position:relative;overflow:hidden}
.lp-regua i b{position:absolute;left:0;top:0;bottom:0;background:var(--verde);opacity:.85}
.lp-regua i s{position:absolute;top:-2px;bottom:-2px;width:2px;background:var(--amar);text-decoration:none}
.lp-regua .dt{display:flex;justify-content:space-between;font-size:.68rem;color:var(--txt-mut);margin-top:.25rem;font-variant-numeric:tabular-nums}
.lp-vazio{color:var(--txt-mut);font-size:.8rem;line-height:1.5;padding:.15rem 0 .3rem}
.lp-chat{display:flex;flex-direction:column;gap:.35rem;padding:.1rem 0 .3rem}
.lp-chat .cx-m{font-size:.8rem}
.lp-tl{margin:.1rem 0 0;padding:0;list-style:none}
.lp-tl li{display:grid;grid-template-columns:5.6rem 1fr;gap:.6rem;font-size:.8rem;padding:.18rem 0;line-height:1.45}
.lp-tl li .q{color:var(--txt-mut);font-family:var(--mono);font-size:.7rem;padding-top:.1rem}
.lp-pdf{font-size:.68rem;font-weight:700;letter-spacing:.04em;padding:.06rem .38rem;border-radius:5px;background:var(--azul-fundo);border:1px solid var(--azul-borda);color:#8FC9E6;text-decoration:none}
.lp-chips{display:flex;gap:.35rem;flex-wrap:wrap;margin-top:.15rem}
.lp-foot{border-top:1px solid var(--borda);padding:.5rem .95rem;font-size:.72rem;color:var(--txt-mut);flex:none;display:flex;justify-content:space-between;gap:.5rem;flex-wrap:wrap}
.lp-foot a{color:var(--verde-claro);text-decoration:none}
.perdapop{position:fixed;inset:0;z-index:120;background:rgba(0,0,0,.55);
  display:flex;align-items:center;justify-content:center;padding:1rem}
.perdapop .pp-cx{background:var(--card);border:1px solid var(--borda);border-radius:14px;
  padding:1rem;width:340px;max-width:100%;box-shadow:0 18px 46px rgba(0,0,0,.5)}
.perdapop .pp-t{font-weight:700;font-size:1rem}
.perdapop .pp-sub{color:var(--txt-mut);font-size:.76rem;margin:.2rem 0 .7rem}
.perdapop .pp-lista{display:flex;flex-direction:column;gap:.35rem}
.perdapop .pp-op{width:100%;margin:0;text-align:left;background:var(--bg);color:var(--txt);
  border:1px solid var(--borda);border-radius:9px;padding:.5rem .7rem;font-size:.84rem;cursor:pointer}
.perdapop .pp-op:hover{border-color:var(--verde)}
.perdapop .pp-op.on{border-color:var(--verde);background:var(--neon-fundo);color:var(--verde-claro)}
.perdapop .pp-desc{width:100%;margin-top:.5rem;background:var(--bg);border:1px solid var(--borda);
  color:var(--txt);border-radius:9px;padding:.5rem .7rem;font-size:.84rem}
.perdapop .pp-bts{display:flex;gap:.5rem;margin-top:.8rem}
.perdapop .pp-ok{width:auto;margin:0;background:var(--verde);color:var(--sobre-verde);border:0;
  border-radius:9px;padding:.45rem 1rem;font-weight:600;cursor:pointer}
.perdapop .pp-ok:disabled{opacity:.45;cursor:default}
.perdapop .pp-nao{width:auto;margin:0;background:none;border:1px solid var(--borda);
  color:var(--txt-mut);border-radius:9px;padding:.45rem .9rem;cursor:pointer}
.lp-trecho{border-left:2px solid #1f3a4d;padding:.25rem .55rem;margin-top:.3rem;color:var(--txt-mut);font-style:italic;font-size:.78rem;background:var(--bg)}
.lp-pista{margin-top:.3rem;font-size:.78rem;color:#e0b45f}
.lp-evbts{display:flex;gap:.4rem;margin-top:.45rem}
.lp-evbts .pbtn{width:auto;margin:0;padding:.35rem .7rem;font-size:.78rem}
/* O RESUMO DA IA (25/09/2026, docs/mockups/funil_resumo_ia.html). A MESMA
   moldura da janela do lead (.leadpop): abre inteira, sem cobrir o card, e o
   reload de 60 s do quadro já não fecha nada com .leadpop aberto. Lilás é o que
   a IA escreveu; verde é fato do sistema (quantas mensagens, com quem está a bola).
   Todo botão com margin:0 e width:auto — o button{width:100%} global vaza aqui. */
.lpia-ic{color:var(--roxo)}
/* o button global do painel tem altura mínima de toque; aqui a janela é densa */
.lpia .lpia-re,.lpia .lpia-bt,.lpia .lpia-voto button,.lpia .lpia-faixa button,.lpia .lpia-erro button{min-height:0;line-height:1.25}
.lpia-re{margin:0 0 0 auto;width:28px;height:28px;padding:0;border-radius:50%;border:1px solid var(--borda);background:none;color:var(--txt-mut);cursor:pointer;font-size:.85rem;flex:none}
.lpia-re:hover{color:var(--txt);border-color:var(--roxo)}
.lpia-fatos{display:flex;flex-wrap:wrap;gap:.35rem;margin-top:.5rem}
.lpia-chip{font-size:.7rem;border-radius:999px;padding:.12rem .55rem;border:1px solid var(--borda);color:var(--txt-mut);white-space:nowrap}
.lpia-chip.bola{border-color:var(--ambar-borda);background:var(--ambar-fundo);color:#F2C66E}
.lpia-chip.ok{border-color:var(--neon-borda);background:var(--neon-fundo);color:var(--neon-bright)}
.lpia-b{display:flex;flex-direction:column;gap:.75rem;font-size:.84rem}
.lpia-bl{display:flex;flex-direction:column;gap:.25rem}
.lpia-rot{font-family:var(--mono);font-size:.62rem;letter-spacing:.08em;text-transform:uppercase;color:var(--roxo)}
.lpia-bl ul{margin:0;padding-left:1.05rem;display:flex;flex-direction:column;gap:.2rem}
.lpia-bl li::marker{color:var(--roxo)}
.lpia-passo{border:1px solid #4a3163;background:#1c1428;border-radius:10px;padding:.55rem .7rem;color:#E3CCF2}
.lpia-passo b{color:#fff;font-weight:600}
.lpia-naosei{border:1px dashed var(--ambar-borda);border-radius:10px;padding:.45rem .6rem;font-size:.78rem;color:#F2C66E}
.lpia-caixa{width:100%;min-height:7.5rem;resize:vertical;background:var(--bg);border:1px solid var(--borda);border-radius:10px;color:var(--txt);padding:.55rem .6rem;font:inherit;font-size:.84rem;line-height:1.5;margin:0;box-sizing:border-box}
.lpia-caixa:focus{outline:none;border-color:var(--roxo)}
.lpia-bts{display:flex;flex-wrap:wrap;gap:.4rem}
.lpia-bt{width:auto;margin:0;border:1px solid var(--borda);background:var(--card-2);border-radius:8px;padding:.36rem .7rem;font-size:.78rem;color:var(--txt);cursor:pointer}
.lpia-bt:hover{border-color:var(--roxo)}
.lpia-bt.pri{background:var(--verde);border-color:var(--verde);color:var(--sobre-verde);font-weight:600}
.lpia-bt:disabled{opacity:.5;cursor:default}
.lpia-faixa,.lpia-erro{display:flex;align-items:center;gap:.5rem;border:1px solid var(--ambar-borda);background:var(--ambar-fundo);color:#F2C66E;border-radius:8px;padding:.4rem .55rem;font-size:.76rem}
.lpia-erro{border-color:var(--coral-borda);background:var(--coral-fundo);color:#F5C9C4}
.lpia-faixa button,.lpia-erro button{margin:0 0 0 auto;width:auto;border:1px solid currentColor;background:none;color:inherit;border-radius:6px;padding:.1rem .45rem;font-size:.72rem;cursor:pointer;white-space:nowrap}
.lpia-sk{height:9px;border-radius:5px;background:linear-gradient(90deg,var(--card-2),var(--borda),var(--card-2));background-size:200% 100%;animation:lpiask 1.4s linear infinite}
@keyframes lpiask{to{background-position:-200% 0}}
@media (prefers-reduced-motion:reduce){.lpia-sk{animation:none}}
.lpia-le{color:var(--txt-mut);font-size:.76rem}
.lpia-voto{display:flex;gap:.25rem}
.lpia-voto button{width:auto;margin:0;border:1px solid var(--borda);background:none;border-radius:7px;padding:.12rem .4rem;font-size:.75rem;cursor:pointer}
.lpia-voto button.on{border-color:var(--roxo);background:#1c1428}
.lp-ab.lpia-ab{background:#1c1428;border-color:#4a3163;color:#E3CCF2;cursor:pointer;width:auto;margin:0;font-family:inherit}"""

JS = r"""// O ESCAPE, SEM DEPENDER DO BALÃO DE CONVERSA (19/09/2026). `cxEscK` nasceu em
// web/balao_conversa.py, e este módulo o usava emprestado — o que funcionava só
// porque as duas telas que abriam a janela (funil e Follow-up) carregam os DOIS
// módulos. A Comunicação carrega só este, e sem o escape uma aspa no nome de um
// lead quebraria o HTML da janela inteira.
// `||` e não redeclaração: onde o balão já definiu o dele, o dele continua
// valendo — é a MESMA função, e duas declarações do mesmo nome numa página só
// pedem confusão na hora de achar quem está no ar.
window.cxEscK = window.cxEscK || function(s){
  var d=document.createElement('div'); d.textContent=(s==null?'':s); return d.innerHTML; };
// confirmar o que o leitor achou: vira "✓ confirmado" no card
function kbLeadConfirmarEvento(id){
  zapFetch('/painel/prospeccao/'+id+'/evento/confirmar',{method:'POST',headers:{'X-Requested-With':'fetch'},body:new FormData()})
    .then(function(d){if(!d)return;if(!d.ok){zapAviso(d.erro||'Não consegui confirmar.',{tipo:'mal'});return;}location.reload();});}
// O balão do LEAD — resumo pra decidir a próxima ação (ligar, chamar no
// WhatsApp, mudar a situação, ver o que aconteceu por último). Antes o clique
// no card abria uma gaveta de 1080px com a ficha INTEIRA num iframe (edição de
// cadastro, IA de primeiro contato, decisor Credify, orçamento) — pesado pra
// só decidir o que fazer agora. Isso fica só na ficha completa, atrás do link
// "Ver ficha completa". Mesmo mecanismo de posicionamento/fechar do balão de
// chat (ver comentário em kbAbrirChat) — duplicado de propósito, não
// compartilhado: são popovers independentes, cada um fecha só o que é seu.
// O ✕ SOBREVIVE AO ERRO (19/09/2026). Os quatro caminhos de erro faziam
// `pop.innerHTML='<div class=cx-empty>…</div>'`, o que APAGAVA o botão de fechar
// junto com o "Carregando…": quem batia num erro ficava com um popover que só
// sai no Esc ou no clique fora — e ninguém adivinha isso olhando uma caixa com
// uma frase dentro. O recado entra junto do ✕, não no lugar dele.
function _leadPopErro(txt){
  return '<button type="button" class="pop-close" title="Fechar" onclick="kbFecharLead()">✕</button>'
    + '<div class="cx-empty">' + txt + '</div>';
}
var _leadPop=null;
function kbFecharLead(){
  if(_leadPop){_leadPop.remove();_leadPop=null;}
  document.body.classList.remove('lp-aberta');
  document.removeEventListener('click',_leadPopFora,true);
  document.removeEventListener('keydown',_leadPopEsc,true);
  window.removeEventListener('scroll',_leadPopRolou,true);
  window.removeEventListener('resize',_leadPopRedimensionou);
}
function _leadPopRedimensionou(){if(_leadPop)_leadPopPosiciona(_leadPop);}
// ONDE A JANELA ABRE (25/09/2026). Decide pelo tamanho DO CONTEÚDO, medido — não
// pelo espaço que sobra. Roda duas vezes: no "Carregando…" e de novo quando o
// resumo chega, que é quando a janela ganha a altura de verdade.
//
//   1. cabe inteira embaixo do botão → embaixo (o de sempre);
//   2. senão, cabe inteira em cima → em cima;
//   3. senão, em nenhum dos dois → AO LADO do botão (à esquerda; à direita se
//      não houver espaço), centrada nele e empurrada pra dentro da tela.
//      Ao lado, e não por cima: por cima, o ponteiro que acabou de clicar em
//      "Abrir ficha" caía em cima de um chip — no teste, o "✕ Perdido" — e um
//      clique duplo encerrava o lead sem ninguém querer;
//   4. tela estreita demais pra ir ao lado (celular) → por cima, inteira. No
//      toque não existe ponteiro parado em cima de nada.
//
// A altura máxima é a tela menos as margens: só quando nem isso basta é que a
// janela rola (o `overflow-y` da `.leadpop`).
function _leadPopPosiciona(pop){
  var el=pop._ancora; if(!el||!el.getBoundingClientRect)return;
  var r=el.getBoundingClientRect(), MARG=8, GAP=6;
  var vw=window.innerWidth, vh=window.innerHeight;
  var larg=Math.min(378, vw-2*MARG);
  pop.style.left=Math.max(MARG,Math.min(r.left,vw-larg-MARG))+'px';
  pop.style.bottom='';
  pop.style.maxHeight=(vh-2*MARG)+'px';
  pop.style.top=MARG+'px';                      // mede sem esbarrar no pé da tela
  var h=pop.offsetHeight;
  var abaixo=vh-r.bottom-GAP-MARG, acima=r.top-GAP-MARG, top;
  if(h<=abaixo) top=r.bottom+GAP;
  else if(h<=acima) top=r.top-GAP-h;
  else{
    top=Math.max(MARG,Math.min(vh-MARG-h, r.top+r.height/2-h/2));
    var esq=r.left-GAP-larg, dir=r.right+GAP;
    if(esq>=MARG) pop.style.left=Math.round(esq)+'px';
    else if(dir+larg<=vw-MARG) pop.style.left=Math.round(dir)+'px';
  }
  pop.style.top=Math.round(top)+'px';
}
// SELECIONAR E SOLTAR FORA NÃO FECHA (25/09/2026). A janela do resumo tem uma
// caixa de texto pra editar: arrastar de dentro dela pra selecionar e soltar o
// mouse fora gera um click FORA, e a edição sumia. O click só fecha se o
// mousedown também começou fora.
var _leadPopDentro=false;
document.addEventListener('mousedown',function(e){_leadPopDentro=!!(_leadPop&&_leadPop.contains(e.target));},true);
function _leadPopFora(e){if(_leadPopDentro){_leadPopDentro=false;return;}if(_leadPop&&!_leadPop.contains(e.target))kbFecharLead();}
function _leadPopEsc(e){if(e.key==='Escape')kbFecharLead();}
function _leadPopRolou(e){if(_leadPop&&_leadPop.contains(e.target))return;kbFecharLead();}
function kbAbrirLead(ev,id,cardEl){
  if(ev)ev.stopPropagation();
  // O BALÃO DE CONVERSA PODE NÃO EXISTIR NESTA PÁGINA (19/09/2026). Era
  // `kbFecharChat()` cru, e funcionava enquanto as únicas telas que abriam a
  // janela — funil e Follow-up — também carregavam web/balao_conversa.py. A
  // Comunicação tem o chat PRÓPRIO dela e carrega só a janela: ali o nome não
  // existe, e um `ReferenceError` na PRIMEIRA linha matava a função inteira. O
  // botão parecia morto — nada abria, nada aparecia no console de quem usa.
  // O `kbAbrirSegurado`, mais novo, já nasceu com esta guarda; esta linha é a
  // que ficou para trás. Dois popovers independentes: fechar o outro é cortesia,
  // não pré-requisito.
  if(window.kbFecharChat)kbFecharChat();
  kbFecharLead();
  var pop=document.createElement('div');
  pop.className='leadpop';
  pop._ancora=cardEl;
  pop.innerHTML='<button type="button" class="pop-close" title="Fechar" onclick="kbFecharLead()">✕</button><div class="cx-empty">Carregando…</div>';
  document.body.appendChild(pop);
  _leadPop=pop;
  document.body.classList.add('lp-aberta');
  _leadPopPosiciona(pop);
  setTimeout(function(){document.addEventListener('click',_leadPopFora,true);document.addEventListener('keydown',_leadPopEsc,true);
    window.addEventListener('scroll',_leadPopRolou,true);window.addEventListener('resize',_leadPopRedimensionou);},0);
  zapFetch('/painel/prospeccao/'+id+'/resumo').then(function(d){
    if(_leadPop!==pop)return;
    // `d` nulo = o zapFetch já explicou o que houve, com o recado daquele caso.
    // A caixa aqui só diz que a janela não tem o que mostrar.
    if(!d||!d.ok){pop.innerHTML=_leadPopErro(d?'Não consegui abrir.':'Não deu pra carregar.');_leadPopPosiciona(pop);return;}
    pop._d=d;
    pop.innerHTML=kbLeadHtml(d,id);
    _leadPopPosiciona(pop);
  });
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
  h+='</div>';
  // A FILEIRA DE ATALHOS SÓ EXISTE SE TIVER ATALHO. Enquanto o seletor de situação
  // morava aqui dentro ela nunca ficava vazia; agora que ele saiu, um lead sem
  // telefone, WhatsApp, Instagram nem endereço deixaria na tela uma tarja com
  // borda e nada dentro.
  var at='';
  if(d.tel_link)at+='<a class="lp-ab" href="'+cxEscK(d.tel_link)+'">📞 Ligar</a>';
  if(d.zap_link)at+='<a class="lp-ab" href="'+cxEscK(d.zap_link)+'" target="_blank" rel="noopener">💬 WhatsApp</a>';
  if(d.insta_url)at+='<a class="lp-ab" href="'+cxEscK(d.insta_url)+'" target="_blank" rel="noopener">📷 Instagram</a>';
  if(d.maps_url)at+='<a class="lp-ab" href="'+cxEscK(d.maps_url)+'" target="_blank" rel="noopener">🗺️ Mapa</a>';
  // o resumo da IA, só pra quem já conversou (sem mensagem não há o que resumir)
  if(d.ia_resumo&&(d.canais_contato||[]).length)at+='<button type="button" class="lp-ab lpia-ab" onclick="kbLeadResumoIA(event,'+(+id)+')">✨ Resumo IA</button>';
  if(at)h+='<div class="lp-acoes">'+at+'</div>';
  h+=kbLeadSitHtml(d,id)+'<div class="lp-body">'
    +'<div id="lp-view">'+kbLeadDadosHtml(d,id)+kbLeadHistHtml(d)+'</div>'
    +'<div id="lp-edit" style="display:none">'+kbLeadEditHtml(d,id)+'</div>'
    +'</div>'
    +'<a class="lp-mais" target="_blank" href="/painel/prospeccao/'+id+'">Ver ficha completa ↗</a>';
  return h;
}
// O BLOCO DA SITUAÇÃO: uma fileira de botões, não uma roleta.
//
// Três decisões, e as três vieram de medir o funil em produção em 17/09/2026
// (este JS é servido a toda conta — nome de cliente fica no commit, não aqui):
//
//   1. A LISTA VEM INTEIRA (`lista_de_status` no Python). Até esse dia, abrindo
//      pelo quadro, a etapa marcada "sai do quadro" não aparecia — e em duas das
//      contas com o funil renomeado isso incluía o próprio `ganho`. O vendedor
//      não conseguia marcar a venda de onde ele trabalha. Aqui elas voltam,
//      tracejadas e com o aviso no title.
//   2. O PASSO SEGUINTE nasce aceso (classe `prox`): é a etapa logo depois da
//      atual no caminho, o toque que a pessoa quase sempre veio dar.
//   3. GANHO E PERDIDO SAEM DA FILEIRA e vão pra uma linha própria, depois de um
//      traço, em verde e vermelho. Numa lista única os dois ficavam encostados na
//      etapa de fechamento: um dedo torto no celular marcava como perda um lead
//      recém-ganho, disparava a pergunta do motivo e tirava o card do quadro.
//
// SEM PALAVRA DE NICHO AQUI DENTRO, nem em comentário (CLAUDE.md §6): este JS é
// servido inteiro pra toda conta, e o teste do vocabulário em
// tests/test_follow_up.py confere a PÁGINA RENDERIZADA, comentário incluído —
// foi ele que pegou a primeira versão deste bloco, que citava um nicho só.
function kbLeadSitHtml(d,id){
  var lista=window._KB_STATUS||[];
  if(!lista.length)return '';
  // o caminho é tudo que NÃO é desfecho; o índice guardado é o da lista ORIGINAL,
  // porque é por ele que o clique encontra a etapa de volta (número no onclick não
  // precisa de escape, chave de texto precisaria)
  var caminho=[],fins=[],atual=-1;
  lista.forEach(function(s,i){ (s.fim?fins:caminho).push({s:s,i:i}); });
  caminho.forEach(function(x,k){ if(x.s.c===d.status)atual=k; });
  var prox=(atual>=0&&atual+1<caminho.length)?caminho[atual+1].s.c:null;
  function chip(x,extra){
    var s=x.s,cls='lp-chip'+(extra||'')+(s.c===d.status?' on':'')
      +(s.c===prox?' prox':'')+(s.sai?' sai':'');
    var t=s.sai?' title="ao entrar aqui o card sai do quadro"':'';
    return '<button type="button" class="'+cls+'"'+t+' onclick="kbLeadIr(this,'+id+','+x.i+')">'
      +(s.fim==='ganho'?'✓ ':s.fim==='perdido'?'✕ ':'')+cxEscK(s.r)+'</button>';
  }
  var h='<div class="lp-sit"><div class="lp-sit-h"><b>Situação</b>'
    +(d.parado_txt?('<em>'+cxEscK(d.parado_txt)+'</em>'):'')+'</div><div class="lp-chips">';
  caminho.forEach(function(x){h+=chip(x,'');});
  h+='</div>';
  if(fins.length){
    h+='<div class="lp-sit-h fim"><b>Encerrar</b></div><div class="lp-chips">';
    fins.forEach(function(x){h+=chip(x,' fim '+x.s.fim);});
    h+='</div>';
  }
  return h+'</div>';
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
  zapFetch('/painel/prospeccao/'+id+'/editar-rapido',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:body})
    .then(function(d){
      if(!_leadPop)return;
      if(!d||!d.ok){
        if(btn){btn.disabled=false;btn.textContent='Salvar';}
        if(d)zapAviso('Não consegui salvar.',{tipo:'mal'});
        return;
      }
      return zapFetch('/painel/prospeccao/'+id+'/resumo').then(function(d2){
        if(!_leadPop||!d2||!d2.ok)return;
        _leadPop._d=d2;
        _leadPop.innerHTML=kbLeadHtml(d2,id);
      });
    });
}
// A mesma rota da ficha completa (fichaStatus lá dentro) — só troca a situação
// e, se deu certo, move o card pra coluna nova no board por trás (mesma
// varredura de contagem do drag-and-drop) e fecha o balão: o resultado
// (card na coluna nova) já fica visível sem o balão flutuando desalinhado.
// A LISTA QUE FALTAVA NO PAINEL (17/09/2026).
//
// O servidor sempre soube recusar: quando a etapa exige motivo, `/status` devolve
// {ok:false, erro:'motivo_obrigatorio', motivos:[...]} — com a lista pronta. O
// painel JOGAVA ISSO FORA. Arrastar o card pra Perdido recarregava a página em
// silêncio (o card voltava sozinho, sem uma palavra); o seletor daqui dizia só
// "Não consegui mudar a situação", que não diz o que fazer.
//
// Foi o dono quem viu o efeito, no Raio-X: "3 de 5 sem motivo". O bloco explicava
// que o motivo é um toque numa lista de seis "no app e na ficha" — e essa frase
// era, sem querer, a confissão: no QUADRO, que é onde o card se move, lista
// nenhuma. Quem trabalha ali ou perdia o lead sem motivo (quando a exigência
// estava desligada) ou batia numa parede muda (depois de ligada).
//
// Mora no módulo compartilhado porque as duas telas que abrem a janela precisam
// dela, e porque o arrastar do quadro chama a MESMA função — uma cópia por tela
// seria a terceira versão da mesma pergunta.
// `url` (opcional, 18/09/2026): a folha "Por que perdeu?" passou a servir também à
// APÓLICE do segurado (web/painel_apolices), que perde renovação com a mesma lista
// de motivos do funil. Sem `url` continua indo pro status do lead, como sempre —
// copiar a folha pra mudar uma linha seria a segunda cópia que diverge.
function kbPerguntarMotivo(id, status, lista, quandoOk, quandoDesiste, url){
  var ant=document.getElementById('perdapop'); if(ant)ant.remove();
  var pop=document.createElement('div'); pop.className='perdapop'; pop.id='perdapop';
  var h='<div class="pp-cx"><div class="pp-t">Por que perdeu?</div>'
    +'<div class="pp-sub">Sem isto o relatório do dono não sabe dizer o que está derrubando as vendas.</div>'
    +'<div class="pp-lista">';
  (lista||[]).forEach(function(m){
    h+='<button type="button" class="pp-op" data-chave="'+cxEscK(m.chave)+'" data-desc="'
      +(m.exige_descricao?'1':'0')+'">'+cxEscK(m.rotulo)+'</button>';
  });
  h+='</div><input class="pp-desc" id="pp-desc" placeholder="Conte em uma linha o que houve" style="display:none">'
    +'<div class="pp-bts"><button type="button" class="pp-ok" disabled>Confirmar</button>'
    +'<button type="button" class="pp-nao">Deixar como estava</button></div></div>';
  pop.innerHTML=h; document.body.appendChild(pop);
  var escolhido=null, desc=pop.querySelector('#pp-desc'), ok=pop.querySelector('.pp-ok');
  function fecha(){pop.remove();}
  pop.querySelectorAll('.pp-op').forEach(function(b){
    b.addEventListener('click',function(){
      pop.querySelectorAll('.pp-op').forEach(function(o){o.classList.remove('on');});
      b.classList.add('on'); escolhido=b.getAttribute('data-chave');
      // O CAMPO DE TEXTO NASCE VISÍVEL só quando o motivo escolhido pede — e some
      // quando não pede. Um campo sempre aberto vira ruído; sempre fechado esconde
      // a exigência até o servidor recusar de novo.
      desc.style.display = b.getAttribute('data-desc')==='1' ? 'block' : 'none';
      ok.disabled=false;
    });
  });
  ok.addEventListener('click',function(){
    if(!escolhido)return;
    ok.disabled=true; ok.textContent='Salvando…';
    var body=new URLSearchParams();
    body.append('status',status); body.append('motivo',escolhido);
    body.append('perda_descricao', desc.style.display==='none' ? '' : desc.value);
    if(url)body.append('json','1');
    zapFetch(url||('/painel/prospeccao/'+id+'/status'),{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:body})
      .then(function(d2){
        if(!d2){ok.disabled=false;ok.textContent='Confirmar';return;}
        if(!d2.ok){
          ok.disabled=false; ok.textContent='Confirmar';
          // `descricao_obrigatoria` volta SEM lista: o motivo já está escolhido e
          // o que falta é a linha de texto. Mostrar a lista de novo aqui faria a
          // pessoa recomeçar do zero por causa de um campo.
          if(d2.erro==='descricao_obrigatoria'){desc.style.display='block';desc.focus();return;}
          zapAviso('Não consegui marcar como perdido.',{tipo:'mal'}); return;
        }
        fecha(); quandoOk(d2);
      });
  });
  pop.querySelector('.pp-nao').addEventListener('click',function(){fecha();if(quandoDesiste)quandoDesiste();});
}
// UM TOQUE MUDA A SITUAÇÃO. Substituiu o `kbLeadStatus(sel,id)` do <select> em
// 17/09/2026 — o resto do caminho é o mesmo de antes, de propósito: a MESMA rota
// da ficha completa, a MESMA folha de "por que perdeu" que o arrastar do quadro
// usa, o MESMO gancho de depois.
//
// O ÍNDICE (e não a chave) vai no onclick porque número não precisa de escape:
// rótulo e chave vêm do cliente, e uma aspa no meio quebraria o atributo.
//
// A FILEIRA INTEIRA TRAVA enquanto o servidor decide. Sem isso, dois toques
// seguidos em chips diferentes mandam duas trocas e quem responder por último
// vence — com a tela mostrando a primeira.
function kbLeadIr(btn,id,idx){
  var s=(window._KB_STATUS||[])[idx];
  if(!s||btn.disabled||btn.classList.contains('on'))return;
  var novo=s.c, caixa=btn.closest('.lp-sit');
  function trava(v){ if(caixa)caixa.querySelectorAll('.lp-chip').forEach(function(b){b.disabled=v;}); }
  function solta(){ trava(false); btn.classList.remove('indo'); }
  trava(true); btn.classList.add('indo');
  function pronto(d){
    // O QUE ACONTECE DEPOIS é de cada tela, e por isso é um gancho e não um `if`
    // por tela aqui dentro: no funil o card anda pra coluna nova sem recarregar
    // nada; no Follow-up a troca pode TIRAR o lead da lista (ganho e perdido saem
    // do funil) e mexe nos quatro números do topo, então a resposta honesta é
    // recarregar. Tela sem gancho recarrega — o lado seguro de errar é mostrar o
    // estado novo, nunca o antigo.
    if(typeof window.kbDepoisDoStatus==='function'){window.kbDepoisDoStatus(d,id,novo);kbFecharLead();return;}
    location.reload();
  }
  var body=new URLSearchParams();body.append('status',novo);
  zapFetch('/painel/prospeccao/'+id+'/status',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:body})
    .then(function(d){
      if(!d){solta();return;}
      if(!d.ok&&d.erro==='motivo_obrigatorio'){
        // desiste = a folha fechou sem escolher motivo. Não há o que desfazer:
        // o chip antigo continua aceso porque nada mudou no servidor.
        solta();
        kbPerguntarMotivo(id, novo, d.motivos, pronto, function(){});
        return;
      }
      if(!d.ok){solta();zapAviso(d.msg||'Não consegui mudar a situação.',{tipo:'mal'});return;}
      pronto(d);
    });
}
// ─────────────────────────────────────────────────────────────────────────────
// O MODO SEGURADO (18/09/2026). A mesma janela, o conteúdo do nicho seguros.
// Aprovado em docs/mockups/ficha_segurado_janela.html: "não quero que mude de
// página nem carregue outra". Quem chama é a Carteira e a fila de Renovações
// (web/painel_apolices): kbAbrirSegurado(ev, cliente_id, linha, 'cliente'|'apolice').
// Precisa de `window._KB_MOTIVOS` (a lista de motivos de perda da conta) pra o
// "Perdi" perguntar por quê — sem ela o chip perdi avisa, não quebra.
function kbAbrirSegurado(ev,id,el,aba){
  if(ev)ev.stopPropagation();
  if(window.kbFecharChat)kbFecharChat();
  kbFecharLead();
  var pop=document.createElement('div');
  pop.className='leadpop'; pop.setAttribute('role','dialog');
  pop._ancora=el;
  pop.innerHTML='<button type="button" class="pop-close" title="Fechar" onclick="kbFecharLead()">✕</button><div class="cx-empty">Carregando…</div>';
  document.body.appendChild(pop);
  _leadPop=pop;
  document.body.classList.add('lp-aberta');
  _leadPopPosiciona(pop);
  setTimeout(function(){document.addEventListener('click',_leadPopFora,true);document.addEventListener('keydown',_leadPopEsc,true);
    window.addEventListener('scroll',_leadPopRolou,true);window.addEventListener('resize',_leadPopRedimensionou);},0);
  zapFetch('/painel/renovacoes/cliente/'+id+'/resumo').then(function(d){
    if(_leadPop!==pop)return;
    if(!d||!d.ok){pop.innerHTML=_leadPopErro(d?'Não consegui abrir.':'Não deu pra carregar.');_leadPopPosiciona(pop);return;}
    pop._d=d; pop._aba=aba||'cliente';
    pop.innerHTML=kbSegHtml(d,pop._aba);
    kbSegLigarConversa(pop);
    _leadPopPosiciona(pop);
  });
}
function kbSegLigarConversa(pop){
  var b=pop.querySelector('.lp-abrir-conversa'); if(!b)return;
  b.addEventListener('click',function(ev){
    var cid=parseInt(b.getAttribute('data-conv'),10);
    // MESMA ARMADILHA DO `kbFecharChat` (19/09/2026), achada pelo teste que
    // nasceu daquela: `kbAbrirChat` mora no balão de conversa, e hoje quem abre
    // a janela do segurado (Renovações) carrega os dois módulos. Se um dia ela
    // abrir de uma tela que carrega só a janela, este clique seria um
    // ReferenceError — botão morto, sem aviso nenhum.
    // Sem o balão, a conversa ainda tem casa: a Comunicação abre pelo id
    // (`?abrir=`). Trocar o balão por uma página é pior; por nada é bem pior.
    if(window.kbAbrirChat){ kbAbrirChat(ev, cid, 'conversas', b, b.getAttribute('data-nome')); return; }
    if(cid) location.href='/painel/prospeccao/comunicacao?abrir='+cid;
  });
}
function kbSegTrocar(aba){
  var pop=_leadPop; if(!pop||!pop._d)return; pop._aba=aba;
  pop.querySelector('.lp-body').innerHTML=(aba==='apolice'?kbSegApoliceHtml(pop._d):kbSegClienteHtml(pop._d));
  pop.querySelectorAll('.lp-tabs button').forEach(function(b){b.classList.toggle('on',b.getAttribute('data-aba')===aba);});
  pop.querySelector('.lp-body').scrollTop=0;
  kbSegLigarConversa(pop);
  _leadPopPosiciona(pop);        // a outra aba tem outra altura
}
// o estado da carteira, numa palavra: é o único lugar da janela onde a cor fala
function kbSegEstado(d){
  var p=d.proxima;
  if(p&&p.dias<=60)return '<span class="lp-estado regua">renova em '+p.dias+' dias</span>';
  if(p)return '<span class="lp-estado ok">renova '+cxEscK(p.vence)+'</span>';
  if(d.apolices.length&&!d.n_vivas)return '<span class="lp-estado mal">sem apólice viva</span>';
  return '';
}
function kbSegHtml(d,aba){
  var zap = d.zap_link ? '<a class="lp-ab" style="border-color:var(--verde);color:var(--verde-claro)" href="'+cxEscK(d.zap_link)+'" target="_blank" rel="noopener">🟢 WhatsApp</a>' : '';
  var tel = d.telefone ? '<a class="lp-ab" href="tel:'+cxEscK((d.telefone||'').replace(/\D/g,''))+'">📞 '+cxEscK(d.telefone)+'</a>' : '';
  var sub = [d.documento, (d.tipo||'pf').toUpperCase(), d.desde?('cliente desde '+d.desde):''].filter(Boolean).join(' · ');
  return '<button type="button" class="pop-close" title="Fechar" onclick="kbFecharLead()">✕</button>'
   +'<div class="lp-h lp-h-seg"><div class="top"><h3>'+cxEscK(d.nome)+'</h3>'+kbSegEstado(d)+'</div>'
   +'<div class="sub">'+cxEscK(sub)+'</div></div>'
   +(zap||tel?'<div class="lp-acoes">'+zap+tel+'</div>':'')
   +'<div class="lp-tabs"><button type="button" data-aba="cliente" class="'+(aba!=='apolice'?'on':'')+'" onclick="kbSegTrocar(\'cliente\')">Cliente</button>'
   +'<button type="button" data-aba="apolice" class="'+(aba==='apolice'?'on':'')+'" onclick="kbSegTrocar(\'apolice\')">Apólice'+(d.apolices.length>1?' · '+d.apolices.length:'')+'</button></div>'
   +'<div class="lp-body">'+(aba==='apolice'?kbSegApoliceHtml(d):kbSegClienteHtml(d))+'</div>'
   +'<div class="lp-foot"><span>Editar, arquivar e fundir ficam na ficha completa.</span><a href="/painel/clientes/'+d.id+'">ficha completa →</a></div>';
}
function kbSegApResumo(a){
  var estado = a.viva ? (a.dias<0?'<span class="lp-estado mal">venceu</span>':(a.dias<=60?'<span class="lp-estado regua">'+a.dias+' dias</span>':''))
                      : '<span class="lp-estado'+(a.situacao==='renovada'?' ok':(a.situacao==='perdida'?' mal':''))+'">'+cxEscK(a.situacao_txt)+'</span>';
  var pct = a.pct_vigencia==null ? 0 : Math.max(0,Math.min(100,a.pct_vigencia));
  var com = a.comissao_fmt ? cxEscK(a.comissao_fmt.replace(/ \(.*\)$/,'')) : '<span style="color:var(--amar)">falta o %</span>';
  return '<div class="lp-ap" onclick="kbSegTrocar(\'apolice\')" title="abrir a apólice">'
   +'<div class="t">'+cxEscK(a.seguradora)+' · '+cxEscK(a.ramo_txt).toLowerCase()+(a.tem_pdf?' <span class="lp-pdf">PDF</span>':'')+estado+'</div>'
   +(a.bem_txt?'<div class="m">'+cxEscK(a.bem_txt)+(a.classe_bonus?' · bônus '+cxEscK(a.classe_bonus):'')+'</div>':'')
   +'<div class="d"><span><b>'+cxEscK(a.vence)+'</b>vence</span><span><b>'+cxEscK(a.premio_fmt)+'</b>prêmio</span>'+(a.viva?'<span><b>'+com+'</b>comissão</span>':'')+'</div>'
   +(a.viva&&a.inicio?'<div class="lp-regua"><i><b style="width:'+pct+'%"></b><s style="left:83.6%"></s></i><div class="dt"><span>'+cxEscK(a.inicio)+'</span><span>'+cxEscK(a.vence)+'</span></div></div>':'')
   +'</div>';
}
function kbSegClienteHtml(d){
  var s='<div class="lp-sec"><div class="st"><b>Contato</b></div><div class="lp-fato">'
   +'<span class="k">telefone</span><span class="v">'+cxEscK(d.telefone||'—')+'</span>'
   +'<span class="k">e-mail</span><span class="v">'+cxEscK(d.email||'—')+'</span>'
   +'<span class="k">endereço</span><span class="v">'+cxEscK(d.endereco_fmt||'—')+'</span></div></div>';
  s+='<div class="lp-sec"><div class="st"><b>Apólices</b><span>'+(d.apolices.length?d.n_vivas+' viva'+(d.n_vivas===1?'':'s')+(d.premio_ano_fmt&&d.n_vivas?' · '+cxEscK(d.premio_ano_fmt)+'/ano':''):'')+'</span></div>';
  if(!d.apolices.length)s+='<div class="lp-vazio">Nenhuma apólice cadastrada pra este cliente.</div>';
  d.apolices.forEach(function(a){s+=kbSegApResumo(a);});
  s+='</div>';
  s+='<div class="lp-sec"><div class="st"><b>Conversa</b>'+(d.conversa.length?'<span>últimas '+d.conversa.length+'</span>':'')+'</div>';
  if(!d.conversa.length){s+='<div class="lp-vazio">Este número ainda não falou com o chip. Quando falar, as últimas mensagens aparecem aqui.</div>';}
  else{
    s+='<div class="lp-chat">';
    d.conversa.forEach(function(m){ s+='<div class="cx-m'+(m.de==='ele'?' cin':'')+'">'+cxEscK(m.texto)+'<span class="meta">'+cxEscK(m.quando)+'</span></div>'; });
    s+='</div>';
    if(d.conversa_id && window.kbAbrirChat){
      // SEM onclick inline, de propósito: o nome vai em data-attribute e o clique é
      // ligado em kbSegLigarConversa. Dispensa JSON dentro de atributo e evita que
      // este JS — que o Follow-up também injeta — carregue a chamada inline do balão
      // que aquela tela usa como prova de que lead sem conversa não virou botão.
      s+='<div style="margin-top:.35rem"><button type="button" class="lp-ab lp-abrir-conversa" style="margin:0;width:auto" data-conv="'+parseInt(d.conversa_id,10)+'" data-nome="'+cxEscK(d.nome||'')+'">💬 abrir conversa inteira</button></div>';
    }
  }
  s+='</div>';
  s+='<div class="lp-sec"><div class="st"><b>Linha do tempo</b></div>';
  if(!d.linha_do_tempo.length)s+='<div class="lp-vazio">Nada registrado ainda.</div>';
  else{s+='<ul class="lp-tl">';d.linha_do_tempo.forEach(function(t){s+='<li><span class="q">'+cxEscK(t.quando)+'</span><span>'+cxEscK(t.texto)+'</span></li>';});s+='</ul>';}
  s+='</div>';
  return s;
}
function kbSegApoliceHtml(d){
  if(!d.apolices.length)return '<div class="lp-sec"><div class="lp-vazio">Nenhuma apólice cadastrada pra este cliente.</div></div>';
  var s='';
  d.apolices.forEach(function(a){
    var estado = a.viva ? (a.dias<0?'<span class="lp-estado mal">venceu há '+(-a.dias)+' dias</span>':(a.dias<=60?'<span class="lp-estado regua">faltam '+a.dias+' dias</span>':'<span class="lp-estado">faltam '+a.dias+' dias</span>'))
                        : '<span class="lp-estado'+(a.situacao==='renovada'?' ok':(a.situacao==='perdida'?' mal':''))+'">'+cxEscK(a.situacao_txt)+'</span>';
    s+='<div class="lp-sec"><div class="st"><b>'+cxEscK(a.seguradora)+' · '+cxEscK(a.ramo_txt).toLowerCase()+(a.tem_pdf?' <span class="lp-pdf">PDF</span>':'')+'</b>'+estado+'</div>'
     +'<div class="lp-fato">'
     +'<span class="k">vigência</span><span class="v">'+cxEscK(a.inicio||'?')+' → <b>'+cxEscK(a.vence)+'</b></span>'
     +(a.regua_txt?'<span class="k">régua</span><span class="v">'+cxEscK(a.regua_txt)+'</span>':'')
     +'<span class="k">prêmio</span><span class="v">'+cxEscK(a.premio_fmt)+(a.iof_fmt?' <span style="color:var(--txt-mut)">+ '+cxEscK(a.iof_fmt)+' IOF/juros</span>':'')+'</span>'
     +(a.parcelas_txt?'<span class="k">parcelas</span><span class="v">'+cxEscK(a.parcelas_txt)+'</span>':'')
     +'<span class="k">comissão</span><span class="v">'+(a.comissao_fmt?cxEscK(a.comissao_fmt):'<span style="color:var(--amar)">sem percentual cadastrado para '+cxEscK(a.seguradora)+'</span>')+'</span>'
     +(a.numero_txt?'<span class="k">número</span><span class="v mono">'+cxEscK(a.numero_txt.replace(/^(apólice|proposta) /,''))+' <span style="font-family:inherit;color:var(--txt-mut)">'+cxEscK(a.numero_txt.split(' ')[0])+'</span></span>':'')
     +(a.bem_txt?'<span class="k">bem</span><span class="v">'+cxEscK(a.bem_txt)+'</span>':'')
     +(a.chassi?'<span class="k">chassi</span><span class="v mono">'+cxEscK(a.chassi)+'</span>':'')
     +(a.classe_bonus?'<span class="k">bônus</span><span class="v">classe '+cxEscK(a.classe_bonus)+'</span>':'')
     +'<span class="k">documento</span><span class="v">'+(a.tem_pdf?'<a class="lp-pdf" href="/painel/renovacoes/apolice/'+a.id+'/pdf" target="_blank">PDF</a> <a href="/painel/renovacoes/apolice/'+a.id+'/pdf" target="_blank" style="color:var(--verde-claro);text-decoration:none">ver o PDF ↗</a> <span style="color:var(--txt-mut)">· coberturas ficam nele</span>':'<span style="color:var(--txt-mut)">sem PDF — cadastrada à mão</span>')+'</span>'
     +(a.perda_motivo_txt?'<span class="k">perdida</span><span class="v">'+cxEscK(a.perda_motivo_txt)+'</span>':'')
     +'</div>'
     +'<div class="st" style="margin-top:.7rem"><b>Situação</b></div><div class="lp-chips" data-ap="'+a.id+'">';
    (window._KB_DECISOES||[]).forEach(function(x){
      var on = x.c===a.situacao;
      s+='<button type="button" class="lp-chip'+(on?' on':'')+'" style="margin:0;width:auto" '+(on?'disabled':'')+' onclick="kbSegSituacao(this,'+a.id+',\''+cxEscK(x.c)+'\')">'+cxEscK(x.r)+'</button>';
    });
    s+='</div></div>';
  });
  s+='<div class="lp-sec" style="text-align:center;padding-top:.6rem"><a class="lp-ab" href="/painel/renovacoes#nova" style="display:inline-flex">📄 nova apólice desta pessoa</a></div>';
  return s;
}
// O CHIP DE SITUAÇÃO DA APÓLICE. "perdi" abre a MESMA folha de "Por que perdeu?" do
// funil (kbPerguntarMotivo), apontada pra rota da apólice; o resto grava direto.
// "Renovada" só fecha esta — a nova apólice chega como documento (decisão do
// dono, 18/09: quem emite é a seguradora; nada é fabricado).
function kbSegSituacao(btn,apId,sit){
  var caixa=btn.parentNode, url='/painel/renovacoes/apolice/'+apId+'/situacao';
  function trava(v){caixa.querySelectorAll('.lp-chip').forEach(function(b){b.disabled=v;});}
  function depois(){ if(_leadPop&&_leadPop._d){ zapFetch('/painel/renovacoes/cliente/'+_leadPop._d.id+'/resumo')
      .then(function(d){ if(!_leadPop||!d||!d.ok)return; _leadPop._d=d; kbSegTrocar(_leadPop._aba||'apolice'); }); } }
  if(sit==='perdida'){
    var lista=window._KB_MOTIVOS||[];
    if(!lista.length){alert('Cadastre os motivos de perda na Régua do funil antes de marcar como perdida.');return;}
    kbPerguntarMotivo(apId,'perdida',lista,function(){depois();},null,url);
    return;
  }
  trava(true);
  var body=new URLSearchParams(); body.append('situacao',sit); body.append('json','1');
  zapFetch(url,{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:body})
    .then(function(d){ if(!d){trava(false);return;}
      if(!d.ok){trava(false);zapAviso('Não consegui mudar a situação.',{tipo:'mal'});return;} depois(); });
}
// ─────────────────────────────────────────────────────────────────────────────
// O RESUMO DA IA (25/09/2026, docs/mockups/funil_resumo_ia.html, aprovado).
// Abre pelo ⋯ do card (kbMenu, no quadro) e pelo botão da ficha. É a MESMA
// moldura da janela do lead: `_leadPop`, `_leadPopPosiciona`, o mesmo fechar.
//
// DUAS IDAS AO SERVIDOR, DE PROPÓSITO. O GET só lê o que está guardado e não
// gasta IA; se não há resumo (ou o vendedor pede ↻), a janela faz o POST. Assim
// abrir de novo o mesmo lead é de graça (decisão 2 do dono: só no clique).
//
// "USAR NA CONVERSA" SÓ PREENCHE (decisão 1): põe o texto no campo da conversa e
// quem aperta enviar é o vendedor. Nada aqui envia mensagem.
function kbLeadResumoIA(ev,id){
  var anc=_leadPop&&_leadPop._ancora;
  kbAbrirResumoIA(ev,id,anc);
}
function kbAbrirResumoIA(ev,id,ancora){
  if(ev&&ev.stopPropagation)ev.stopPropagation();
  if(window.kbFecharChat)window.kbFecharChat();
  kbFecharLead();
  var pop=document.createElement('div');
  pop.className='leadpop lpia'; pop.setAttribute('role','dialog');
  pop._ancora=ancora; pop._lead=id; pop._gerando=false; pop._falha='';
  pop.innerHTML='<button type="button" class="pop-close" title="Fechar" onclick="kbFecharLead()">✕</button>'
    +'<div class="lp-body lpia-b">'+_lpiaCarregando()+'</div>';
  document.body.appendChild(pop);
  _leadPop=pop;
  document.body.classList.add('lp-aberta');
  _leadPopPosiciona(pop);
  setTimeout(function(){document.addEventListener('click',_leadPopFora,true);document.addEventListener('keydown',_leadPopEsc,true);
    window.addEventListener('scroll',_leadPopRolou,true);window.addEventListener('resize',_leadPopRedimensionou);},0);
  zapFetch('/painel/prospeccao/'+id+'/resumo-ia').then(function(d){
    if(_leadPop!==pop)return;
    if(!d){pop.innerHTML=_leadPopErro('Não deu pra abrir.');_leadPopPosiciona(pop);return;}
    if(!d.ok){pop.innerHTML=_leadPopErro(cxEscK(d.msg||'Não consegui abrir.'));_leadPopPosiciona(pop);return;}
    pop._d=d;
    if(!d.resumo&&d.tem_conversa&&d.ia){_lpiaGerar(pop,{});return;}
    _lpiaPinta(pop);
  });
}
function _lpiaCarregando(){
  return '<div class="lpia-bl"><div class="lpia-rot">✨ Lendo a conversa…</div>'
    +'<div class="lpia-sk" style="width:92%"></div><div class="lpia-sk" style="width:74%"></div>'
    +'<div class="lpia-sk" style="width:84%"></div><div class="lpia-sk" style="width:60%"></div></div>';
}
function _lpiaGerar(pop,op){
  pop._gerando=true; pop._falha=''; pop._refaz='resumo'; _lpiaPinta(pop);
  var fd=new FormData(); if(op.forcar)fd.append('forcar','1'); if(op.variar)fd.append('variar','1');
  zapFetch('/painel/prospeccao/'+pop._lead+'/resumo-ia',{method:'POST',headers:{'X-Requested-With':'fetch'},body:fd}).then(function(r){
    if(_leadPop!==pop)return;
    pop._gerando=false;
    if(!r){pop._falha='Não consegui ler a conversa agora. Tente de novo em instantes.';_lpiaPinta(pop);return;}
    if(!r.ok){pop._falha=r.msg||'Não consegui ler a conversa agora.';if(r.fatos)pop._d=r;_lpiaPinta(pop);return;}
    pop._d=r; _lpiaPinta(pop);
  });
}
function _lpiaLista(itens){
  var h='<ul>'; (itens||[]).forEach(function(t){h+='<li>'+cxEscK(t)+'</li>';}); return h+'</ul>';
}
function _lpiaPinta(pop){
  var d=pop._d||{}, L=d.lead||{}, F=d.fatos||{}, R=d.resumo, B=F.bola||{};
  var h='<button type="button" class="pop-close" title="Fechar" onclick="kbFecharLead()">✕</button>'
    +'<div class="lp-h"><div class="top"><span class="lpia-ic">✨</span><h3>'+cxEscK(L.nome||'Lead')+'</h3>'
    +(R&&!pop._gerando?'<button type="button" class="lpia-re" title="Ler a conversa de novo" onclick="kbResumoIARefazer()">↻</button>':'')
    +'</div>';
  var sub=[L.etapa,L.vendedor].filter(function(x){return !!x;}).join(' · ');
  if(sub)h+='<div class="sub">'+cxEscK(sub)+'</div>';
  var ch='';
  if(B.txt)ch+='<span class="lpia-chip'+(B.quem==='voce'?' bola':'')+'">'+cxEscK(B.txt)+'</span>';
  if(F.n_total)ch+='<span class="lpia-chip ok">'+(F.n_total>F.n_lidas?'leu as '+F.n_lidas+' mais recentes de '+F.n_total:'leu as '+F.n_total+' mensage'+(F.n_total===1?'m':'ns'))+'</span>';
  if(R&&d.feito_txt&&!pop._gerando)ch+='<span class="lpia-chip">'+cxEscK(d.feito_txt)+'</span>';
  if(ch)h+='<div class="lpia-fatos">'+ch+'</div>';
  h+='</div><div class="lp-body lpia-b">';
  if(d.novas&&R&&!pop._gerando)h+='<div class="lpia-faixa">'+d.novas+(d.novas===1?' mensagem nova':' mensagens novas')+' depois deste resumo<button type="button" onclick="kbResumoIARefazer()">↻ Atualizar</button></div>';
  else if(d.mudou&&R&&!pop._gerando)h+='<div class="lpia-faixa">A conversa mudou depois deste resumo<button type="button" onclick="kbResumoIARefazer()">↻ Atualizar</button></div>';
  if(pop._falha)h+='<div class="lpia-erro">'+cxEscK(pop._falha)+'<button type="button" onclick="'+(pop._refaz==='primeira'?'kbResumoIAPrimeira(null)':'kbResumoIARefazer()')+'">Tentar de novo</button></div>';
  if(pop._gerando)h+=_lpiaCarregando();
  else if(!d.tem_conversa)h+=_lpiaSemConversa(pop);
  else if(R)h+=_lpiaResumoHtml(R,d);
  else if(!d.ia)h+='<div class="lpia-le">A IA não está ligada nesta conta.</div>';
  h+='</div>';
  if(R&&!pop._gerando&&d.tem_conversa){
    h+='<div class="lp-foot"><span>A IA leu só a conversa e o card deste lead. Nada é enviado sozinho.</span>'
      +'<span class="lpia-voto"><button type="button" title="Ajudou" class="'+(d.voto===1?'on':'')+'" onclick="kbResumoIAVoto(1)">👍</button>'
      +'<button type="button" title="Não ajudou" class="'+(d.voto===-1?'on':'')+'" onclick="kbResumoIAVoto(-1)">👎</button></span></div>';
  }
  pop.innerHTML=h;
  _leadPopPosiciona(pop);
}
function _lpiaResumoHtml(R,d){
  var h='';
  if(R.quer)h+='<div class="lpia-bl"><div class="lpia-rot">✨ O que o cliente quer</div><div>'+cxEscK(R.quer)+'</div></div>';
  if((R.em_que_pe||[]).length)h+='<div class="lpia-bl"><div class="lpia-rot">✨ Em que pé está</div>'+_lpiaLista(R.em_que_pe)+'</div>';
  if((R.pode_travar||[]).length)h+='<div class="lpia-bl"><div class="lpia-rot">✨ O que pode travar</div>'+_lpiaLista(R.pode_travar)+'</div>';
  if(R.proximo_passo)h+='<div class="lpia-passo"><div class="lpia-rot">✨ Próximo passo</div><b>'+cxEscK(R.proximo_passo)+'</b></div>';
  (R.nao_sei||[]).forEach(function(t){h+='<div class="lpia-naosei">'+cxEscK(t)+'</div>';});
  if(R.mensagem){
    h+='<div class="lpia-bl"><div class="lpia-rot">✨ Mensagem sugerida · dá pra editar</div>'
      +'<textarea class="lpia-caixa" id="lpia-msg" aria-label="Mensagem sugerida">'+cxEscK(R.mensagem)+'</textarea>'
      +'<div class="lpia-bts"><button type="button" class="lpia-bt pri" onclick="kbResumoIAUsar(event)">Usar na conversa</button>'
      +'<button type="button" class="lpia-bt" onclick="kbResumoIACopiar(this)">Copiar</button>'
      +'<button type="button" class="lpia-bt" onclick="kbResumoIAOutra()">↻ Outra versão</button></div></div>';
  }
  return h;
}
function _lpiaSemConversa(pop){
  var h='<div class="lpia-bl"><div>Este lead ainda não trocou mensagem, então não há conversa pra resumir.</div>';
  if(pop._primeira){
    h+='<div class="lpia-rot">✨ Primeira mensagem · dá pra editar</div>'
      +'<textarea class="lpia-caixa" id="lpia-msg" aria-label="Primeira mensagem">'+cxEscK(pop._primeira.texto||'')+'</textarea>'
      +'<div class="lpia-bts">'+(pop._primeira.link?'<a class="lpia-bt pri" style="text-decoration:none" href="'+cxEscK(pop._primeira.link)+'" target="_blank" rel="noopener" onclick="kbResumoIAZap(this)">Abrir no WhatsApp</a>':'')
      +'<button type="button" class="lpia-bt" onclick="kbResumoIACopiar(this)">Copiar</button></div>';
  }else if((pop._d||{}).ia){
    h+='<div class="lpia-bts"><button type="button" class="lpia-bt" onclick="kbResumoIAPrimeira(this)">✨ Sugerir a primeira mensagem</button></div>';
  }
  return h+'</div>';
}
// o wa.me leva o texto que está na caixa AGORA, não o que a IA escreveu
function kbResumoIAZap(a){
  var ta=_leadPop&&_leadPop.querySelector('#lpia-msg'); if(!ta||!a)return;
  a.href=a.href.split('?')[0]+'?text='+encodeURIComponent(ta.value);
}
function kbResumoIARefazer(){var pop=_leadPop;if(pop&&pop._lead&&!pop._gerando)_lpiaGerar(pop,{forcar:1});}
function kbResumoIAOutra(){var pop=_leadPop;if(pop&&pop._lead&&!pop._gerando)_lpiaGerar(pop,{variar:1});}
function kbResumoIAVoto(v){
  var pop=_leadPop; if(!pop||!pop._d||!pop._d.resumo_id)return;
  var fd=new FormData(); fd.append('voto',String(v));
  zapFetch('/painel/prospeccao/'+pop._lead+'/resumo-ia/'+pop._d.resumo_id+'/voto',{method:'POST',headers:{'X-Requested-With':'fetch'},body:fd}).then(function(r){
    if(!r||!r.ok||_leadPop!==pop)return;
    pop._d.voto=v; pop.querySelectorAll('.lpia-voto button').forEach(function(b,i){b.classList.toggle('on',(i===0&&v===1)||(i===1&&v===-1));});
  });
}
function kbResumoIACopiar(btn){
  var ta=_leadPop&&_leadPop.querySelector('#lpia-msg'); if(!ta)return;
  function ok(){if(btn){var t=btn.textContent;btn.textContent='Copiado';setTimeout(function(){btn.textContent=t;},1600);}}
  try{navigator.clipboard.writeText(ta.value).then(ok,function(){ta.select();});}catch(e){ta.select();}
}
function kbResumoIAPrimeira(btn){
  var pop=_leadPop; if(!pop||!pop._lead)return;
  if(btn){btn.disabled=true;btn.textContent='Escrevendo…';}
  var fd=new FormData(); fd.append('canal','whatsapp');
  zapFetch('/painel/prospeccao/'+pop._lead+'/mensagem-ia',{method:'POST',headers:{'X-Requested-With':'fetch'},body:fd}).then(function(r){
    if(_leadPop!==pop)return;
    if(!r||!r.ok){pop._falha=(r&&r.erro==='sem_ia')?'A IA não está ligada nesta conta.':'Não consegui escrever agora. Tente de novo em instantes.';pop._refaz='primeira';pop._primeira=null;_lpiaPinta(pop);return;}
    pop._primeira={texto:r.texto||'',link:r.link||''}; pop._falha=''; _lpiaPinta(pop);
  });
}
// ONDE O TEXTO ENTRA, em ordem: (1) a Comunicação com a conversa deste lead já
// aberta ao lado; (2) o balão da conversa, onde ele existe (quadro, Follow-up);
// (3) em nenhum dos dois, o texto vai pra área de transferência e a janela diz.
// O balão só aceita o texto se a conversa puder ser respondida por aqui.
function kbResumoIAUsar(ev){
  var pop=_leadPop; if(!pop||!pop._d)return;
  var ta=pop.querySelector('#lpia-msg'), txt=ta?ta.value:''; if(!txt)return;
  var d=pop._d, conv=d.conv, anc=pop._ancora, id=pop._lead;
  if(d.resumo_id)zapFetch('/painel/prospeccao/'+id+'/resumo-ia/'+d.resumo_id+'/usado',{method:'POST',headers:{'X-Requested-With':'fetch'},silencioso:true});
  var cx=document.getElementById('cx-reply');
  if(cx&&conv&&window._cxConv&&(+window._cxConv)===(+conv.id)){
    kbFecharLead(); cx.value=txt; cx.focus();
    try{cx.dispatchEvent(new Event('input'));}catch(e){}
    return;
  }
  if(conv&&window.kbAbrirChat&&anc&&document.body.contains(anc)){
    // de reserva, na área de transferência: se a conversa não responde por aqui
    // (canal sem configuração), o balão não tem campo, e o texto não se perde
    try{navigator.clipboard.writeText(txt).catch(function(){});}catch(e){}
    window._cpPrefill=txt;
    window.kbAbrirChat(ev||{stopPropagation:function(){}},conv.id,conv.aba,anc,(d.lead||{}).nome);
    return;
  }
  var b=pop.querySelector('.lpia-bt.pri');
  try{navigator.clipboard.writeText(txt).then(function(){if(b)b.textContent='Copiei. Cole na conversa';},function(){ta.select();});}catch(e){ta.select();}
}
"""


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
