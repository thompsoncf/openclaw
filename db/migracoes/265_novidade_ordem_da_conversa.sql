-- 265_novidade_ordem_da_conversa.sql
-- A Fila passa a abrir na ordem da conversa, e cada card diz quando a pessoa falou.
--
-- O QUE MUDOU NA TELA (/cockpit)
-- 1) Um seletor no topo, com duas ordens:
--      Por conversa (PADRÃO) — uma lista só, a última mensagem na frente
--      Por urgência          — os grupos de antes (sua vez / festa / sem data / parados)
-- 2) Cada card ganha a coluna da direita: QUANDO em cima, quantas esperam embaixo.
--    A hora escala como no WhatsApp: 07:42 hoje, "ontem", "seg" na semana, 14/09
--    depois. Quem falou HOJE vem em verde.
-- 3) Na ordem por conversa não há recorte de mês — e por isso as pílulas de mês
--    e as de "fora" não aparecem lá. "Tudo", "com proposta" e "com data" ficam.
--
-- A CONTA QUE MOTIVOU, carteira do Pedro Yan em 16/09/2026, 147 leads abertos:
-- a Bianca Sousa escreveu às 07:42 — a mensagem mais recente da carteira inteira
-- — e caía na POSIÇÃO 43. Umas oito telas de rolagem num celular. As oito
-- primeiras por conversa caíam nas posições 43, 1, 24, 26, 32, 44, 45 e 21,
-- porque o grupo "festa marcada" ordena pela data da FESTA e vinha inteiro na
-- frente de quem tinha acabado de falar.
--
-- E a FLAVIA nem aparecia: entrou em 17/08, escreveu em 14/09, e a Fila abria no
-- mês corrente. Mesma armadilha da busca do #698 — um recorte que esconde
-- justamente quem está falando com você.
--
-- POR QUE DUAS ORDENS E NÃO UMA. A ordem por urgência responde "o que eu faço
-- agora" e responde bem; o defeito era ser a ÚNICA. Quando o cliente escreve, a
-- pergunta do vendedor deixa de ser essa e passa a ser "cadê quem acabou de falar
-- comigo". Duas perguntas, duas ordens, nenhuma some. O padrão é a da conversa
-- porque é a que ele usa o dia inteiro, no aplicativo ao lado.
--
-- O PORTÃO: `servico`. Ordem por conversa e hora na lista valem em qualquer nicho
-- que tenha funil — toda conta tem conversa. Dentro da tela, "Por urgência" muda
-- de forma sozinha: sem `vendas.vende_data` não existe "festa marcada", e o grupo
-- "sem data" já vira "Os outros" (§6). Medido nas duas pontas: na conta 34 os
-- quatro formatos de hora estão vivos (2 falaram hoje, 18 ontem, 88 na semana,
-- 246 há mais de 7 dias); na ZAQ 251 de 252 caem em "mais de 7 dias", e ali a
-- coluna vira data — que é exatamente a informação que importa.
--
-- PRA QUEM: vendedor. É a tela dele.
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('fila-na-ordem-da-conversa', 'mudanca', 'servico', '{vendedor}',
 'A Fila abre na ordem da conversa, como no WhatsApp',
 'A lista de leads passou a vir com quem falou por último na frente, e cada cartão agora mostra quando foi a última mensagem.',
 '/cockpit',
 $txt$Quem falou com você por último agora é o primeiro da lista. Como no WhatsApp.

ANTES a Fila vinha separada em grupos — sua vez, festa marcada, sem data, parados — e dentro de "festa marcada" a ordem era a data da FESTA. Na prática isso enterrava quem tinha acabado de escrever: medimos numa carteira de 147 leads e a pessoa com a mensagem mais recente do dia estava na 43ª posição. Umas oito telas de rolagem, com o cliente na linha.

AGORA TEM DUAS ORDENS, num seletor no alto:

"Por conversa" — o padrão. Uma lista só, a última mensagem na frente. É por onde o dia começa.

"Por urgência" — os grupos de antes, do jeito que você já conhece. Continua ali pra quando você quiser planejar a semana pelas festas mais próximas.

E CADA CARTÃO PASSA A DIZER QUANDO FOI, na direita: a hora se foi hoje, "ontem", o dia da semana se foi nesta semana, e a data se for mais antigo. Quem falou hoje aparece em verde. A bolinha vermelha de mensagens sem resposta desceu pra baixo da hora, na mesma coluna.

UM DETALHE: na ordem por conversa não existe filtro de mês. É de propósito — a pergunta ali é "quem falou por último", não "quem entrou este mês", e o filtro escondia gente que tinha acabado de escrever só porque entrou no mês passado. As pílulas de mês continuam na ordem por urgência.$txt$,
 timestamptz '2026-09-16 14:30:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'fila-na-ordem-da-conversa';
