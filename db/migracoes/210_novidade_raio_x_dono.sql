-- 210_novidade_raio_x_dono.sql
-- Os avisos da Peça 3 do Raio-X (seção 5 do CLAUDE.md). Precisa da 199.
--
-- QUEM RECEBE
--   raio-x-do-dono (todos · dono, gestor)   quem tem o painel: a tela nova
--                                           /painel/raio-x, com os filtros e os
--                                           blocos. Também conta que a ficha do
--                                           lead ganhou "de onde veio o cliente"
--                                           e "por que perdeu".
--   motivo-de-perda-no-app (todos · vendedor)  o que muda na rotina dele: ao
--                                           marcar perdido, o motivo é uma lista
--                                           de seis; na ficha, de onde o cliente
--                                           veio. Nada de tela que ele não tem.
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('raio-x-do-dono', 'novidade', 'todos', '{dono,gestor}',
 'O Raio-X do dono, com filtros',
 'O painel ganhou o Raio-X: leads, primeira resposta, propostas, contratos e visitas do período, por vendedor, tipo de festa, mês, dia, convidados, origem e hora que chegou.',
 '/painel/raio-x',
 $txt$O mesmo Raio-X que o vendedor tem no app, agora na sua tela, com os cortes que fazem diferença.

Em cima, o placar do período: leads (com o dia e a hora de pico), primeira resposta (mediana, separando horário comercial de noite e fim de semana), propostas enviadas e em rascunho, contratos assinados e aprovados sem assinatura, e quantas visitas aconteceram. Cada número comparado com o período anterior.

A barra de filtros: período, vendedor, tipo de festa (com "sem tipo" como filtro próprio), mês da festa, dia da festa (sábado contra o resto), faixa de convidados, de onde veio o cliente, e a hora que chegou.

Embaixo, o que o Zaq enriquece sozinho: demanda contra agenda por mês (leads pedindo o mês contra festas marcadas), o dia da semana das festas pedidas, ticket por tipo de festa, quantos dias do lead à proposta e da proposta ao contrato, e por que perdeu. Tudo com a confiança do dado no pé.

Pro "por que perdeu" funcionar, a ficha do lead ganhou dois campos: "de onde veio o cliente" e "por que perdeu" (lista de seis). O vendedor marca no app ao dar o lead como perdido.

Uma linha por vendedor, como no grupo de segunda, fecha a tela.$txt$,
 timestamptz '2026-09-05 22:00:00+00'),

('motivo-de-perda-no-app', 'novidade', 'todos', '{vendedor}',
 'Perdido agora pede o motivo, numa lista',
 'Ao marcar um lead como perdido, o motivo virou uma lista de seis opções. E a ficha pergunta de onde o cliente veio.',
 '/cockpit',
 $txt$Ao marcar um lead como perdido, o motivo deixou de ser texto livre: é uma lista de seis (sumiu depois da proposta, data indisponível, achou caro, fora do escopo, sem interesse, outro). Um toque.

Por que isso importa: "data indisponível" vai virar lista de espera por data, e "achou caro" vai alimentar a tabela. Sem o motivo, o Zaq não consegue ajudar.

Na ficha do cliente entrou "de onde veio o cliente": WhatsApp, indicação, Instagram, manual ou outro. É a segunda pergunta da primeira resposta, junto com o tipo de festa.$txt$,
 timestamptz '2026-09-05 22:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave in ('raio-x-do-dono', 'motivo-de-perda-no-app');
