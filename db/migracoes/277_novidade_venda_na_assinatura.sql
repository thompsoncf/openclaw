-- 277_novidade_venda_na_assinatura.sql
-- A venda passa a contar quando o contrato é assinado.
--
-- REGRA DO DONO, 17/09/2026: "só conta como venda quando assinar contrato".
--
-- O QUE MUDA NA TELA (/painel/prospeccao): quando o cliente assina o contrato pelo
-- link, o card anda sozinho pra etapa de ganho. Antes ficava onde estava.
--
-- A MEDIÇÃO que motivou, conta 34, 7 contratos assinados:
--
--   Bianca Oliveira ....... 29/08 ... Evento Realizado   ← a única no lugar
--   Claudia Carvalho ...... 02/09 ... Evento A Realizar
--   Beatriz do Carmo ...... 08/09 ... Evento A Realizar
--   Jacque/Costa .......... 08/09 ... Evento A Realizar
--   Renata Costa .......... 09/09 ... NEGOCIAÇÃO
--   Josiany Rayra ......... 14/09 ... NEGOCIAÇÃO
--   Larissa Rakel ......... 15/09 ... NEGOCIAÇÃO
--
-- Três clientes com contrato assinado apareciam como "em negociação". E a Renata
-- Costa estava na fila de cobrança do follow-up, sendo cobrada "há 5 dias" — cinco
-- dias DEPOIS de ter assinado. O sistema mandava o vendedor correr atrás de quem já
-- tinha fechado.
--
-- O financeiro já abria na assinatura, e o Raio-X já contava a venda pelo contrato.
-- Era só o card que ficava parado.
--
-- O PORTÃO: `eventos`. Contrato com assinatura pelo link é do nicho que vende data;
-- quem vende mensalidade fecha por outro caminho.
--
-- PRA QUEM: dono, gestor e vendedor. O vendedor é quem para de ser cobrado à toa.
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('venda-na-assinatura-do-contrato', 'novidade', 'eventos', '{dono,gestor,vendedor}',
 'Contrato assinado move o card pra venda fechada, sozinho',
 'Quando o cliente assina o contrato pelo link, o lead passa sozinho pra etapa de venda fechada no funil — sem ninguém precisar arrastar.',
 '/painel/prospeccao',
 $txt$Assinatura de contrato agora fecha a venda no funil também.

O QUE ACONTECE

O cliente assina pelo link. O card dele anda sozinho pra sua etapa de venda fechada, e o movimento fica registrado como "contrato assinado" — dá pra separar depois o que o sistema marcou do que alguém arrastou na mão.

POR QUE ISSO FALTAVA

A assinatura já abria o financeiro e já contava como venda no Raio-X. Só o card do funil ficava parado onde estava. Na prática isso significava cliente com contrato assinado aparecendo como "em negociação" pra equipe — e, pior, entrando na fila de cobrança do follow-up dias depois de ter fechado. Alguém sendo mandado correr atrás de quem já assinou.

O QUE O SISTEMA NÃO FAZ

Não anda pra trás. Se o lead já está na etapa de venda fechada, ou em alguma etapa de depois dela — festa a realizar, evento realizado —, ele fica onde está. A assinatura é o piso da venda, não o teto: quem já passou dali teve alguém movendo de propósito, e isso não se desfaz.

Lead marcado como perdido também não é movido automaticamente. Se um perdido assinou contrato, vale olhar o caso: ou foi marcado errado, ou o cliente voltou — e as duas coisas merecem um olho humano, não um automatismo.$txt$,
 timestamptz '2026-09-17 21:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'venda-na-assinatura-do-contrato';
