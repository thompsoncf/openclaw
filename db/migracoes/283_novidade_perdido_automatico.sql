-- 283_novidade_perdido_automatico.sql
-- O Perdido automático.
--
-- O QUE MUDOU NA TELA (/painel/prospeccao, aba Régua): no bloco Estado aparece um
-- quarto interruptor — "Perdido automático". Nasce Desligado. Ligado, ele move
-- para Perdido o lead que passou do prazo da etapa E não respondeu aos toques,
-- com motivo registrado na ficha e linha no histórico.
--
-- O PORTÃO: `servico`. Quem vende produto não tem funil nem lead pra perder.
--
-- PRA QUEM: dono e gestor. É quem decide fechar em lote — o vendedor vê o card
-- sair do quadro dele, mas não liga nem desliga a regra.
--
-- POR QUE O AVISO IMPORTA AQUI mais que no resto: esta é a primeira regra do
-- produto que TIRA card do quadro sozinho. Quem abrir a Fila no dia seguinte e não
-- souber disso vai achar que perdeu lead.
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('perdido-automatico', 'novidade', 'servico', '{dono,gestor}',
 'O funil agora fecha sozinho quem não respondeu',
 'O lead que passou do prazo da etapa e não respondeu às tentativas de follow-up vai para Perdido sozinho, com o motivo registrado — e quem está esperando resposta sua nunca é fechado.',
 '/painel/prospeccao/regua',
 $txt$A coluna Perdido parou de depender de alguém lembrar de arrastar o card.

COMO LIGAR
Régua do funil › Estado › "Perdido automático". Nasce Desligado. Em Observando, o
sistema conta o que teria fechado sem fechar nada — é assim que dá pra ver o
tamanho antes de valer.

OS TRÊS PORTÕES, E POR QUE SÃO TRÊS
Para um lead ser fechado, as três coisas precisam ser verdade ao mesmo tempo:

1. O prazo da etapa venceu (o teto que você já configura por coluna).
2. A empresa tentou de verdade — o número de toques sem resposta que você define,
   3 por padrão. Sem isso, "não respondeu" carimbaria no cliente uma conversa que
   ninguém puxou.
3. A bola é NOSSA. Se o cliente escreveu por último e ninguém respondeu, ele nunca
   é fechado — em nenhuma hipótese, nem no trigésimo dia.

O terceiro portão foi medido antes de existir: numa conta com 334 leads em
Contatado, 28 estavam parados porque o cliente escreveu e ninguém respondeu.

NADA SE PERDE
Fechar é mudar a coluna e gravar o motivo. A ficha, a conversa inteira e o
histórico continuam onde estavam, e o botão de reabrir devolve o lead para
Contatado num clique. Todo fechamento automático fica marcado como tal no
histórico, separado do que uma pessoa fechou na mão.$txt$,
 now())
on conflict (chave) do nothing;
