"""Guia do vendedor da Prime — como trabalhar pelo celular (app) e pelo computador.

Mesmas regras do comunicado de 03/09: A4, Helvetica, SEM emoji (as fontes
embutidas do reportlab não têm esses glifos e viram quadrado preto). Acento é
normal — Helvetica cobre o Latin-1.

Tudo aqui foi conferido no código antes de escrever:
  abas do app          web/painel_cockpit.py:_abas_vend
  grupos da Fila       finance/cockpit.py:fila_agrupada
  faixas do Raio-X     finance/raio_x.py:responda_hoje
  botões do funil      finance/vendas.py:linha_do_funil
  telas do computador  contas/equipe.py:rotas_do_papel('vendedor')
  atalho do WhatsApp   finance/cockpit.py:entrega_sempre (na Prime é QR: não existe)
  busca e pílulas      finance/cockpit.py:busca_leads_where, contagens_fila, _pilulas
  bloco do contrato    web/painel_cockpit.py (ctr_html) + finance/cockpit.py:enviar_contrato_*

REEDITADO EM 15/09/2026. O passo 6 descrevia um botão verde "Mandar o contrato pra
assinar" que o CELULAR NÃO TINHA: até 15/09 o contrato só saía pelo computador, e
quem lesse o guia no celular procuraria o botão sem achar. Agora ele existe, dentro
do bloco "Contrato" da tela da proposta, e o passo diz o nome certo. Na mesma
rodada a Fila ganhou busca, que entra na seção 1.
"""
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, KeepTogether)

SAIDA = "prime-guia-do-vendedor.pdf"

TINTA   = colors.HexColor("#16191F")
TINTA2  = colors.HexColor("#464E5A")
TINTA3  = colors.HexColor("#79828F")
VERDE   = colors.HexColor("#1B5E4A")   # o celular
VERDE_F = colors.HexColor("#E7F1EC")
AZUL    = colors.HexColor("#1F4E79")   # o computador
AZUL_F  = colors.HexColor("#E9EFF6")
AMBAR   = colors.HexColor("#8A6206")
AMBAR_F = colors.HexColor("#FBF2DE")
LINHA   = colors.HexColor("#DBDFD9")
ZEBRA   = colors.HexColor("#F5F6F4")


def p(nome, **kw):
    base = dict(fontName="Helvetica", fontSize=10.1, leading=14.2, textColor=TINTA2,
                alignment=TA_LEFT, spaceAfter=0)
    base.update(kw)
    return ParagraphStyle(nome, **base)


S = {
    "olho":   p("olho", fontName="Helvetica-Bold", fontSize=7.7, leading=10, textColor=VERDE),
    "titulo": p("titulo", fontName="Helvetica-Bold", fontSize=21.5, leading=25, textColor=TINTA),
    "sub":    p("sub", fontSize=10.9, leading=15.4),
    "h2":     p("h2", fontName="Helvetica-Bold", fontSize=13.5, leading=17.5, textColor=TINTA),
    "h2a":    p("h2a", fontName="Helvetica-Bold", fontSize=13.5, leading=17.5, textColor=AZUL),
    "h2v":    p("h2v", fontName="Helvetica-Bold", fontSize=13.5, leading=17.5, textColor=VERDE),
    "h3":     p("h3", fontName="Helvetica-Bold", fontSize=10.4, leading=13.8, textColor=TINTA),
    "corpo":  p("corpo"),
    "passo":  p("passo", fontSize=9.9, leading=13.4),
    "num":    p("num", fontName="Helvetica-Bold", fontSize=9.9, leading=13.4, textColor=VERDE),
    "cx":     p("cx", fontSize=9.6, leading=13.4),
    "cxt":    p("cxt", fontName="Helvetica-Bold", fontSize=9.6, leading=13.4,
                textColor=TINTA, spaceAfter=5.5),
    "th":     p("th", fontName="Helvetica-Bold", fontSize=8.6, leading=11.4, textColor=TINTA),
    "td":     p("td", fontSize=9.2, leading=12.4),
    "tdb":    p("tdb", fontName="Helvetica-Bold", fontSize=9.2, leading=12.4, textColor=TINTA),
    "sim":    p("sim", fontName="Helvetica-Bold", fontSize=9.2, leading=12.4, textColor=VERDE),
    "nao":    p("nao", fontSize=9.2, leading=12.4, textColor=TINTA3),
    "fala":   p("fala", fontName="Helvetica-Oblique", fontSize=9.8, leading=13.6, textColor=TINTA),
    "rodape": p("rodape", fontSize=8.5, leading=11.8, textColor=TINTA3),
}

LARG = 164 * mm


def caixa(linhas, fundo, borda):
    """Bloco destacado, com a barra colorida à esquerda — separa o aviso do texto
    corrido sem moldura fechada, que pesaria a página."""
    t = Table([[l] for l in linhas], colWidths=[LARG])
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), fundo),
        ("LINEBEFORE",    (0, 0), (0, -1), 2.4, borda),
        ("LEFTPADDING",   (0, 0), (-1, -1), 11),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 11),
        ("TOPPADDING",    (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        # O espaço ENTRE as linhas da caixa sai daqui, e só daqui: `spaceAfter` no
        # estilo do título é ignorado (ele é o último parágrafo da célula dele) e
        # o padding cheio dos dois lados somava 14pt, abrindo uma faixa morta no
        # meio do bloco — o defeito do primeiro comunicado. 4pt é o que separa sem
        # descolar.
        ("BOTTOMPADDING", (0, 0), (-1, -2), 0),
        ("TOPPADDING",    (0, 1), (-1, -1), 4),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
    ]))
    return t


def passos(itens, cor=VERDE):
    est = ParagraphStyle("n2", parent=S["num"], textColor=cor)
    linhas = [[Paragraph(f"{i}.", est), Paragraph(txt, S["passo"])]
              for i, txt in enumerate(itens, 1)]
    t = Table(linhas, colWidths=[7.5 * mm, LARG - 7.5 * mm])
    t.setStyle(TableStyle([
        ("LEFTPADDING",   (0, 0), (-1, -1), 0),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 0),
        ("TOPPADDING",    (0, 0), (-1, -1), 2.6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.6),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
    ]))
    return t


def glossario(pares, cor):
    """Duas colunas: o nome da aba/faixa à esquerda, o que ela é à direita."""
    linhas = [[Paragraph(a, ParagraphStyle("g", parent=S["tdb"], textColor=cor)),
               Paragraph(b, S["td"])] for a, b in pares]
    t = Table(linhas, colWidths=[36 * mm, LARG - 36 * mm])
    t.setStyle(TableStyle([
        ("LEFTPADDING",   (0, 0), (-1, -1), 0),
        ("RIGHTPADDING",  (0, 0), (0, -1), 6),
        ("TOPPADDING",    (0, 0), (-1, -1), 2.8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.8),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW",     (0, 0), (-1, -2), 0.4, LINHA),
    ]))
    return t


def tabela_onde(linhas):
    cab = [Paragraph("O QUE VOCÊ VAI FAZER", S["th"]),
           Paragraph("NO CELULAR", ParagraphStyle("t1", parent=S["th"], textColor=VERDE)),
           Paragraph("NO COMPUTADOR", ParagraphStyle("t2", parent=S["th"], textColor=AZUL))]
    dados = [cab]
    for o_que, cel, comp in linhas:
        dados.append([Paragraph(o_que, S["td"]),
                      Paragraph(cel, S["sim"] if not cel.startswith("—") else S["nao"]),
                      Paragraph(comp, S["sim"] if not comp.startswith("—") else S["nao"])])
    t = Table(dados, colWidths=[LARG - 74 * mm, 37 * mm, 37 * mm], repeatRows=1)
    est = [
        ("LEFTPADDING",   (0, 0), (-1, -1), 6),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
        ("TOPPADDING",    (0, 0), (-1, -1), 4.2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4.2),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW",     (0, 0), (-1, 0), 0.9, TINTA),
        ("LINEBELOW",     (0, 1), (-1, -2), 0.4, LINHA),
    ]
    for i in range(1, len(dados)):
        if i % 2 == 0:
            est.append(("BACKGROUND", (0, i), (-1, i), ZEBRA))
    t.setStyle(TableStyle(est))
    return t


def regua():
    t = Table([[""]], colWidths=[LARG], rowHeights=[0.1])
    t.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, 0), 0.7, LINHA)]))
    return t


doc = SimpleDocTemplate(
    SAIDA, pagesize=A4,
    leftMargin=23 * mm, rightMargin=23 * mm, topMargin=20 * mm, bottomMargin=16 * mm,
    title="Prime Eventos — guia do vendedor",
    author="Prime Eventos", subject="Como trabalhar pelo app e pelo computador")

h = []
A = h.append

A(Paragraph("PRIME EVENTOS &nbsp;&middot;&nbsp; EQUIPE DE VENDAS &nbsp;&middot;&nbsp; "
            "15 DE SETEMBRO DE 2026", S["olho"]))
A(Spacer(1, 8))
A(Paragraph("Como trabalhar no Zaq", S["titulo"]))
A(Spacer(1, 8))
A(Paragraph("O celular é o seu dia: é nele que você responde, marca a data, monta a proposta "
            "e fecha. O computador é onde você senta pra ver a carteira inteira de uma vez. "
            "Este guia diz o que fazer em cada um.", S["sub"]))
A(Spacer(1, 12))
A(caixa([
    Paragraph("A regra de ouro", S["cxt"]),
    Paragraph("<b>Toda conversa com o cliente passa por dentro do Zaq.</b> Nesta conta o Zaq "
              "fala com o cliente a qualquer hora do dia — por isso o app nem mostra atalho "
              "pro WhatsApp do seu celular: não é esquecimento, é de propósito. O que sai por "
              "fora chega sem o nome da Prime, não entra no funil e some quando você troca de "
              "aparelho.", S["cx"]),
], VERDE_F, VERDE))
A(Spacer(1, 13))
A(regua())
A(Spacer(1, 11))

# ──────────────────────────────────────────────────────── 1. celular
A(Paragraph("1. No celular — o app", S["h2v"]))
A(Spacer(1, 6))
A(Paragraph("Abra pelo atalho que está na tela inicial do seu celular. Embaixo tem cinco abas, "
            "e é por elas que o dia anda.", S["corpo"]))
A(Spacer(1, 9))
A(glossario([
    ("Fila", "Todo mundo que está falando com você, já separado por urgência. "
             "É a tela em que o dia começa."),
    ("Agenda", "As festas e as visitas. Dá pra ver só as suas ou as de todo mundo, e filtrar "
               "por <b>Reservado</b> e <b>Pré-reserva</b>."),
    ("Propostas", "Sua carteira de orçamentos, com o valor em jogo. É daqui que você manda a "
                  "proposta e o contrato pro cliente."),
    ("Raio-X", "O que precisa da sua resposta HOJE, e o seu resultado do mês. O número verde "
               "na aba é quanto está esperando você."),
    ("Perfil", "Seus dados, os avisos novos do sistema e o rodízio de leads."),
], VERDE))
A(Spacer(1, 12))

A(KeepTogether([
    Paragraph("A Fila já vem organizada — comece de cima", S["h3"]),
    Spacer(1, 6),
    glossario([
        ("Sua vez", "O cliente falou por último e o agente está desligado. "
                    "<b>É onde o dia começa, sempre.</b>"),
        ("Festa marcada", "Quem já tem dia, na ordem da data. O mais perto primeiro."),
        ("Sem data", "O resto. Aqui a primeira pergunta é sempre o dia da festa."),
        ("Parados", "Quinze dias ou mais sem trocar mensagem. Fica numa dobra fechada, "
                    "pra não atrapalhar — mas não some."),
    ], VERDE),
]))
A(Spacer(1, 12))

A(KeepTogether([
    Paragraph("Procurando alguém? Use a busca, não role a lista", S["h3"]),
    Spacer(1, 6),
    Paragraph("No alto da Fila tem uma caixa de busca. É pra quando o cliente liga "
              "perguntando do orçamento dele e você precisa achar o nome <b>agora</b>.",
              S["corpo"]),
    Spacer(1, 6),
    glossario([
        ("Por nome", "Um pedaço basta, e não precisa de acento: “caro” acha "
                     "“Maria Carolina”, “jacque” acha “Jacqueline”."),
        ("Por número", "Os últimos dígitos bastam. Não precisa do DDD nem do 55."),
        ("Procura em tudo", "A busca <b>ignora o filtro de mês</b> e olha todos os seus leads "
                            "abertos. Quem entrou em agosto aparece igual."),
    ], VERDE),
    Spacer(1, 7),
    Paragraph("E duas pílulas novas, do lado dos meses: <b>“com proposta”</b> (só quem já tem "
              "orçamento feito — é a fila mais curta e a mais perto do dinheiro) e "
              "<b>“com data”</b> (só quem já tem dia marcado).", S["corpo"]),
]))
A(Spacer(1, 12))

A(KeepTogether([
    Paragraph("O Raio-X diz o que responder hoje", S["h3"]),
    Spacer(1, 6),
    Paragraph("Cada lead aparece em um lugar só, na ordem em que aperta:", S["corpo"]),
    Spacer(1, 6),
    glossario([
        ("Pergunta", "O cliente falou e ficou sem resposta. Mostra o que ele disse e há "
                     "quanto tempo."),
        ("Festa perto", "Festa em até 60 dias e ainda sem proposta montada."),
        ("Proposta parada", "Proposta enviada e sem resposta há 3 dias ou mais."),
        ("Toque", "A vez do próximo toque na cadência. O app diz qual é: 2º toque, "
                  "3º toque, porta aberta."),
        ("Visita amanhã", "Visita marcada pra amanhã e ainda sem confirmação."),
    ], VERDE),
]))
A(Spacer(1, 13))
A(regua())
A(Spacer(1, 11))

# ──────────────────────────────────────────────────────── 2. computador
A(KeepTogether([
    Paragraph("2. No computador — o painel", S["h2a"]),
    Spacer(1, 6),
    Paragraph("Entre com o mesmo e-mail e a mesma senha. Você cai direto no app; pra chegar "
              "no painel, use o endereço do painel no navegador. São três telas suas:",
              S["corpo"]),
    Spacer(1, 9),
    glossario([
        ("Serviços", "O funil das suas propostas, uma por linha, com o que falta em cada uma "
                     "e um único botão verde: o próximo passo."),
        ("Prospecção", "O quadro dos seus leads por etapa, pra arrastar e enxergar a "
                       "carteira inteira de uma vez."),
        ("Agenda", "O calendário da casa. É compartilhado de propósito: data prometida duas "
                   "vezes é problema de todo mundo."),
    ], AZUL),
]))
A(Spacer(1, 10))
A(caixa([
    Paragraph("O que você vê no painel é só o seu", S["cxt"]),
    Paragraph("Serviços e Prospecção filtram pela sua carteira — proposta de outro vendedor "
              "não aparece pra você, e a sua não aparece pra ele. A Agenda é a exceção, e é "
              "de propósito. O modelo do contrato quem edita é o Manoel.", S["cx"]),
], AZUL_F, AZUL))
A(Spacer(1, 13))
A(regua())
A(Spacer(1, 11))

# ──────────────────────────────────────────────────────── 3. o caminho
# O CAMINHO VEM ANTES DA TABELA de propósito. Na primeira montagem a tabela vinha
# aqui, e a seção do caminho — que é um bloco só, indivisível — não cabia no que
# sobrava da página: pulava inteira pra seguinte e deixava um terço de página em
# branco no meio do documento. Trocadas de lugar, o caminho fecha a página 2 e a
# tabela vira o cartão de consulta no fim, onde sobra espaço sem incomodar.
#
# A seção inteira num bloco só: partida ao meio, ela punha os passos 1 e 2 no pé de
# uma página e os outros oito na seguinte — e a lista é justamente o que se lê de
# uma vez, com o dedo acompanhando.
A(KeepTogether([
    Paragraph("3. O caminho de uma venda, do começo ao fim", S["h2"]),
    Spacer(1, 6),
    Paragraph("Em qualquer das duas telas o Zaq mostra <b>um único botão verde</b> por proposta: "
              "o próximo passo. Se você não sabe o que fazer, é nele que se toca.", S["corpo"]),
    Spacer(1, 9),
    passos([
    "O lead chega na <b>Fila</b>. Você responde <b>por dentro do app</b>.",
    "Descobre o dia da festa e grava na <b>Ficha</b>. A data vira <b>pré-reserva</b> na agenda "
    "na hora — é isso que impede outro vendedor de prometer o mesmo dia.",
    "Abre o lead e toca em <b>Orçamento</b>. A proposta já nasce numerada.",
    "Botão verde: <b>“Mandar o orçamento pro cliente”</b>.",
    "O cliente <b>aprova</b>. O contrato dele nasce na hora, também numerado.",
    "Na tela da proposta aparece o bloco <b>Contrato</b>. Toque em "
    "<b>“Mandar na conversa”</b> (ou <b>“Mandar por e-mail”</b>). O cliente lê e assina "
    "pelo link, do celular dele.",
    "Só então você <b>manda o Pix da entrada</b>.",
    "Caiu o dinheiro? Botão verde: <b>“Sinal recebido”</b>. "
    "<b>É neste momento que a data fica reservada.</b>",
    "O app te leva direto pra <b>Pagamentos e comprovantes</b>. Anexe o comprovante ali mesmo, "
    "com ele ainda na mão.",
        "Botão verde: <b>“Fechar negócio”</b>. Acabou.",
    ]),
]))
A(Spacer(1, 10))
A(caixa([
    Paragraph("O bloco Contrato diz de quem é a bola", S["cxt"]),
    Paragraph("<b>“Ainda não foi enviado”</b>, em vermelho, quer dizer que ninguém está "
              "esperando o cliente — o cliente é que está esperando você. "
              "<b>“Enviado há 3 dias, sem assinatura”</b> é o contrário: aí sim cabe cobrar. "
              "Medimos aqui na casa: quem recebe assina <b>no mesmo dia</b>. O que atrasa "
              "a venda é o contrato parado aqui dentro.", S["cx"]),
], VERDE_F, VERDE))
A(Spacer(1, 8))
A(caixa([
    Paragraph("O que dizer pro cliente", S["cxt"]),
    Paragraph("“Vou te mandar o contrato pra você ler com calma e assinar. Depois que assinar, "
              "te passo o Pix da entrada — e é a entrada que reserva a sua data.”", S["fala"]),
], VERDE_F, VERDE))
A(Spacer(1, 8))
A(caixa([
    Paragraph("Assinar não segura a data", S["cxt"]),
    Paragraph("<b>A data só fica reservada quando a entrada cai</b> — é a cláusula 4.1, a mesma "
              "de sempre. Assinar o contrato firma o compromisso, mas enquanto o dinheiro não "
              "entra a data continua correndo o risco de outro fechar antes. Diga isso na hora "
              "de mandar o contrato, não depois.", S["cx"]),
], AMBAR_F, AMBAR))
A(Spacer(1, 13))

# ──────────────────────────────────────────────────────── 4. hábitos
A(KeepTogether([
    Paragraph("4. Seis hábitos que fazem a diferença", S["h2"]),
    Spacer(1, 7),
    passos([
        "<b>Comece pela Fila, no grupo “Sua vez”.</b> É gente esperando resposta agora.",
        "<b>Zere o Raio-X antes de encerrar o dia.</b> O número verde na aba é a sua lista "
        "de pendências — quando ele some, o dia fechou.",
        "<b>Data primeiro, proposta depois.</b> Sem o dia gravado não nasce pré-reserva, e "
        "sem pré-reserva a data não é sua.",
        "<b>Comprovante no mesmo minuto do sinal.</b> Depois ninguém lembra, e a proposta "
        "fica marcada como “parcela sem comprovante”.",
        "<b>Contrato aprovado é contrato enviado no mesmo dia.</b> Quem recebe assina na "
        "hora — o que atrasa a venda é o contrato parado aqui dentro.",
        "<b>Nada por fora.</b> O que não passou pelo Zaq não aconteceu: não entra no funil, "
        "não conta no seu resultado e não fica pra ninguém.",
    ]),
]))

A(Spacer(1, 13))
A(regua())
A(Spacer(1, 11))

# ──────────────────────────────────────────────────────── 5. tabela
# Título junto da tabela (e da régua que o anuncia): solto, ele ficava sozinho no
# pé da página anterior, prometendo uma tabela que só vinha depois de virar a folha.
# Sem régua nesta: o bloco cai no alto da página 3, e ali a linha não separa nada
# — fica boiando acima do título como sujeira de impressão.
A(KeepTogether([
    Paragraph("5. Onde eu faço cada coisa", S["h2"]),
    Spacer(1, 7),
    tabela_onde([
    ("Responder o cliente", "Sim — é o lugar", "— não tem"),
    ("Procurar um lead pelo nome ou número", "Sim — é o lugar", "Sim"),
    ("Mandar áudio, foto ou arquivo pro cliente", "Sim", "— não tem"),
    ("Gravar o dia da festa (Ficha)", "Sim", "Sim"),
    ("Montar a proposta", "Sim", "Sim"),
    ("Mandar a proposta pro cliente", "Sim", "Sim"),
    ("Mandar o contrato pra assinar", "Sim", "Sim"),
    ("Confirmar que o sinal caiu", "Sim", "Sim"),
    ("Anexar o comprovante", "Sim", "Sim"),
    ("Marcar visita ou compromisso", "Sim", "Sim"),
    ("Ver a carteira inteira lado a lado", "— apertado", "Sim — é o lugar"),
        ("Mudar o modelo do contrato", "— só o Manoel", "— só o Manoel"),
    ]),
]))
A(Spacer(1, 7))
A(Paragraph("Quase tudo dá pra fazer nos dois. A diferença é o conforto: no celular você "
            "resolve na hora, com o cliente na linha; no computador você enxerga o conjunto.",
            S["corpo"]))
A(Spacer(1, 10))
A(regua())
A(Spacer(1, 9))

A(Spacer(1, 14))
A(regua())
A(Spacer(1, 9))
A(Paragraph("Se o app estiver instalado na tela inicial do celular, feche e abra de novo pra "
            "pegar a versão mais nova. Qualquer coisa que não apareça como está aqui, "
            "avise o Manoel.", S["rodape"]))

doc.build(h)
print("gerado:", SAIDA)
