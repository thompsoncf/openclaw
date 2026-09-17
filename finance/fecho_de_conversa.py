"""A última mensagem do cliente ENCERRA o turno, ou pede resposta?

POR QUE ESTE MÓDULO EXISTE, com a data e o número. Em 16/09/2026 o dono olhou o
funil da Prime e disse: "muito vendedor tá dizendo que vai dar oi do celular pra
mandar fotos, e outros o cliente dá ok obrigado sobre uma resposta — vale analisar
todas as conversas pra ver o sentido de tá crítico ou não".

Fui ler as 57 conversas que o sistema dizia estarem esperando resposta NOSSA:

    30 de 57 (53%)  a última mensagem do cliente era "Ok", "Obrigada",
                    "Tá certo", "Adorei" — cortesia, não pergunta
     3 de 57        tinham ponto de interrogação

E os dois primeiros da coluna Proposta, que o quadro punha no topo por causa da
festa próxima, eram "Obrigada!" (Renata Costa, festa em 11 dias) e "Bgd" (Josiany).

O DEFEITO, em uma linha: `prazo_automatico` decide "o cliente está esperando" por
`ult_in > ult_out` — comparando RELÓGIO. Isso confunde quem PERGUNTOU com quem
AGRADECEU, que são coisas opostas: uma pede resposta, a outra fecha o assunto.
Como o prazo vira `ult_in + bola_nossa_min`, um "Obrigada!" de sete dias atrás
nasce vencido há sete dias — e o card abre a coluna marcado 🚨 Crítico.

POR QUE REBAIXAR É SEGURO, medido antes de escrever: dos 189 encerramentos de
cortesia da história da conta 34, em 161 (85%) O CLIENTE VOLTOU A FALAR SOZINHO.
"Ok, obrigada" não é fim de relação — é fim de TURNO. Tratar como "esperando o
cliente" é a verdade, e é o que a escada de toques já sabe cobrar.

AS TRÊS TRAVAS, porque errar pro outro lado é pior:

  1. Pergunta sempre vence. Tem '?' → nunca é fecho, por mais curta que seja.
  2. Texto longo nunca é fecho. Quem escreveu três linhas disse alguma coisa.
  3. Áudio, foto e documento nunca são fecho — não dá pra saber o que tem dentro,
     e supor que um áudio de 14s é "obrigada" seria esconder um pedido.

E a regra só REBAIXA: ela tira urgência de quem não pediu nada, nunca acrescenta.
Um lead escondido por engano é uma venda perdida em silêncio; um lead cobrado à
toa é um incômodo. Os dois erros não custam o mesmo.

O VOCABULÁRIO É DE PRODUÇÃO, não inventado: saiu das mensagens reais da conta 34.
Está aqui em cima, legível, pra o dono corrigir quando vir uma que não serve —
e não escondido dentro de uma expressão regular.
"""
from __future__ import annotations

import re

#: O que o cliente escreve quando está fechando o assunto. Lido das conversas da
#: Prime (conta 34) em 16/09/2026. Tudo sem acento e em minúscula — a comparação
#: normaliza os dois lados, senão "obrigada" e "Obrigada!" seriam coisas
#: diferentes e metade da lista não valeria nada.
FECHOS = frozenset(x.strip() for x in """
ok | okay | oka | ok ok | blz | beleza | show | massa | otimo | otima | perfeito
isso | isso mesmo | ss | sim | sim sim | claro | uhum | ata | aham
obrigado | obrigada | obg | obgda | bgd | brigado | brigada | vlw | valeu
agradecido | agradecida | grato | grata | gratidao
ta | ta bom | ta bem | ta certo | ta otimo | ta joia | esta bem | estou bem
certo | certinho | combinado | fechado | tranquilo | tudo bem | tudo certo
entendi | entendido | ja entendi | ah sim | ah sim entendi | assim entendi
ate | ate mais | ate logo | ate breve | ate amanha
ok obrigado | ok obrigada | certo obrigado | certo obrigada
mt obg | muito obg | muito obrigado | muito obrigada | ok muito obg
obrigado pelo atendimento | obrigada pelo atendimento
obrigado pela atencao | obrigada pela atencao
obrigado pela indicacao | obrigada pela indicacao
agradeco pela disponibilidade | agradeco a disponibilidade
adorei | amei | gostei | maravilha | maravilhoso | legal | bacana | top
""".strip().replace("\n", " | ").split("|") if x.strip())

#: Acima disto não é cortesia: quem escreveu tanto disse alguma coisa. 40 caracteres
#: depois de normalizar — "obrigada pela disponibilidade" tem 29, e a maior frase
#: de cortesia que achei na conta 34 ("Tudo bem, obrigada pelo atendimento") tem 35.
LIMITE = 40

#: Marcas que o produto põe no texto quando a mensagem NÃO é texto. Um áudio pode
#: ser um "obrigada" ou pode ser o cliente pedindo a proposta — e supor é o erro
#: que este módulo existe pra não cometer.
_NAO_E_TEXTO = ("🎤", "📷", "📄", "🎥", "📎", "🩷", "áudio", "audio", "foto",
                "imagem", "vídeo", "video", "documento", "figurinha", "localização")

_ACENTOS = str.maketrans("áàâãäéèêëíìîïóòôõöúùûüçÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇ",
                         "aaaaaeeeeiiiiooooouuuucAAAAAEEEEIIIIOOOOOUUUUC")


def _normalizar(texto: str) -> str:
    """minúscula, sem acento, sem pontuação/emoji nas pontas, espaços colapsados."""
    t = " ".join((texto or "").split()).translate(_ACENTOS).lower()
    # tira pontuação e emoji das DUAS pontas; o miolo fica intacto pra o
    # comprimento continuar dizendo a verdade sobre o tamanho da frase
    return re.sub(r"^[^\w]+|[^\w]+$", "", t, flags=re.UNICODE)


def eh_fecho(texto: str | None) -> bool:
    """Esta mensagem do cliente encerra o turno (e NÃO está pedindo resposta)?

    Devolve False em tudo que não tiver certeza — ver as três travas no topo do
    módulo. Texto vazio é False: sem texto não há como saber, e o silêncio de um
    campo vazio não pode virar motivo pra parar de cobrar.
    """
    bruto = (texto or "").strip()
    if not bruto:
        return False
    if "?" in bruto:
        return False                      # trava 1: pergunta sempre espera
    baixo = bruto.lower()
    if any(m in baixo for m in _NAO_E_TEXTO):
        return False                      # trava 3: áudio/mídia não se adivinha
    t = _normalizar(bruto)
    if not t or len(t) > LIMITE:
        return False                      # trava 2: frase longa disse alguma coisa
    if t in FECHOS:
        return True
    # "Ok. Obrigada" e "Certo, mt obg!" são DOIS fechos colados. Quebra por
    # pontuação e pelo "e": cada pedaço tem que ser um fecho conhecido, e UM SÓ
    # desconhecido basta pra devolver False. Por isso quebrar mais não afrouxa a
    # regra — "Ok. Mas queria outra data" continua esperando, porque o segundo
    # pedaço não está no vocabulário.
    partes = [p for p in re.split(r"[,;.!]+| e ", t) if p.strip()]
    return len(partes) > 1 and all(p.strip() in FECHOS for p in partes)
