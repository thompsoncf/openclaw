-- 200_novidade_funil_mes_atual.sql
-- Os avisos das quatro entregas de 05/09/2026 (#622, #625, #626, #627), que
-- subiram sem aviso. É o primeiro PR a seguir a seção 5 do CLAUDE.md: PR que
-- muda tela leva o aviso. Precisa da 199 (pra_quem, resumo, link).
--
-- QUEM RECEBE, conferido na produção em 05/09/2026:
--   funil-mes-atual (todos · dono, gestor)     22 contas, todas — e é 'todos' de
--                                              propósito: o funil e a Fila são de
--                                              qualquer conta, sem nicho.
--   card-le-a-data-da-festa (eventos)          conta 34 (Prime Eventos) e conta 35
--                                              (Louana Vanessa). Só quem tem
--                                              `vende_data`, que é o portão da
--                                              linha do evento no card.
--   fila-no-mes-atual (todos · vendedor)       os 9 vendedores das duas contas de
--                                              eventos e mais os das outras que
--                                              usam o app. Quem não tem vendedor
--                                              não tem quem ver.
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

-- ────────────────────────────────────────────── 4. os avisos de hoje
insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

-- Pra quem manda na conta, qualquer conta: o quadro abre no mês.
('funil-mes-atual', 'novidade', 'todos', '{dono,gestor}',
 'O funil abre no mês atual, e a Fila do vendedor também',
 'O funil abre no mês corrente, com o que ficou de fora a um toque. Ninguém sumiu: "Tudo" está sempre ali.',
 '/painel/prospeccao',
 $txt$Agosto trouxe 254 leads e setembro 21, e caía tudo numa coluna só. Agora o quadro abre no mês corrente.

Na barra de cima, as pílulas "Entraram em" escolhem o mês (o corrente já vem ligado, e "Tudo" traz o resto). As duas pílulas amarelas trazem o que ficou de fora: "🟢 esperando resposta", que é o cliente que falou por último, e "🎉 festa em 30 dias". Ligou, entram no quadro marcados com o mês; desligou, saem. A escolha fica guardada por pessoa.

Dentro de cada coluna, "esperando resposta" é o primeiro grupo. Parado há mais de 15 dias vai pra uma dobra fechada no pé, com o botão "perguntar" abrindo a conversa com a pergunta pronta.

A Fila do vendedor no celular faz o mesmo: abre no mês, "sua vez" em cima, festa marcada na ordem da data, e os parados na dobra. O número da aba Fila continua sendo a carteira inteira.

No dia 1º de cada mês o quadro abre quase vazio. É normal: as pílulas amarelas seguram o que não pode esperar.$txt$,
 timestamptz '2026-09-05 12:00:00+00'),

-- Só eventos: é `vende_data` que decide se a linha do evento aparece no card.
('card-le-a-data-da-festa', 'novidade', 'eventos', '{dono,gestor}',
 'O card do funil lê a data da festa na conversa',
 'Quando o cliente escreve "dia 20 de fevereiro, umas 120 pessoas", o card já sabe a data, o tipo e os convidados.',
 '/painel/prospeccao',
 $txt$Cada lead ganhou data, tipo e número de convidados do evento, e o card mostra isso numa linha. Quem já tinha orçamento ligado recebeu a data dele de graça.

O Zaq lê o que o cliente escreve na conversa: "20 de fevereiro", "17 ou 18/12", "150 pessoas", "casamento", "15 anos". A cada mensagem que chega ele preenche só o que está vazio, e o card ganha o selo "💬 lido" com o trecho. Sem IA: é leitura por regra, e quem bate o martelo é o vendedor.

Quem só falou o mês vira uma pista ("falou de março"), com Confirmar e Corrigir. O botão "Ler as conversas" passa pelo acervo inteiro de uma vez.

E o funil ganhou a vista por mês do evento: o botão no topo alterna entre as etapas e os meses das festas, com o trilho "Festa em" filtrando e contando.$txt$,
 timestamptz '2026-09-05 12:00:00+00'),

-- Pro vendedor, no app dele. O link abre a Fila, que é onde a mudança está.
('fila-no-mes-atual', 'novidade', 'todos', '{vendedor}',
 'Sua Fila mudou: abre no mês e separa por sua vez',
 'A Fila abre no mês atual, com "sua vez" em cima, festa marcada na ordem da data e os parados numa dobra.',
 '/cockpit',
 $txt$Antes seus leads vinham numa lista só, do mais recente pro mais antigo. Agora a Fila abre no mês atual e se separa por o que cada lead precisa de você.

"8 de 85" quer dizer 8 leads de setembro, de 85 na sua carteira. Os outros não sumiram: toque em "Ago" ou "Tudo".

🟢 Sua vez vem em cima: é quem falou por último e está esperando você. Comece por aí. 🎉 Festa marcada vem na ordem da data, 📅 Sem data logo abaixo, e os parados há 15 dias ficam na dobra do pé.

As pílulas amarelas trazem o que ficou de fora: "🟢 sua vez" traz quem está esperando você em qualquer mês, e "🎉 30 dias" traz festa que está chegando. Quem entra por elas vem marcado com o mês. O app lembra a sua escolha.

O card mostra a data da festa que o cliente falou na conversa, com o selo "💬 lido". Se ele só falou o mês, a conversa abre com um aviso pra você confirmar; "sem data" tem o botão "perguntar", que deixa a pergunta pronta na caixa.$txt$,
 timestamptz '2026-09-05 12:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave in ('funil-mes-atual',
--     'card-le-a-data-da-festa', 'fila-no-mes-atual');
