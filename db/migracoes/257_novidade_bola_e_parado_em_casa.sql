-- 257_novidade_bola_e_parado_em_casa.sql
-- Raio-X: o bloco de assinatura passa a dizer de quem é a bola, e nasce a métrica
-- "parado em casa".
--
-- O QUE MUDOU NA TELA
-- 1) O bloco "Aprovado, esperando assinatura" virou DOIS grupos:
--      A bola está com você    — contrato pronto e nunca enviado (coral)
--      A bola está com o cliente — enviado, aguardando assinatura (âmbar)
--    Cada linha mostra os DOIS documentos, com data e por quem:
--      ✓ Orçamento · aprovado 10/09 por Maria Carolina da Silva Costa
--      ● Contrato nº 8 · pronto há 4 dias — nunca enviado
-- 2) O relógio passa a contar do EVENTO CERTO: do envio pra quem foi enviado, da
--    criação do contrato pra quem não foi. Antes contava da aprovação sempre.
-- 3) KPI de contratos ganha "parado em casa: N dias (mediana) · M sem enviar
--    agora".
--
-- A MEDIÇÃO QUE MOTIVOU, conta 34, 14/09/2026. Separei o tempo dos 6 contratos
-- assinados em dois pedaços:
--     parado em casa (criado -> enviado)      35 dias somados
--     esperando o cliente (enviado -> assin.)  1 dia somado
-- Todo cliente assinou NO MESMO DIA em que recebeu — só um assinou no dia
-- seguinte. A demora nunca foi do cliente:
--     Josiany  23 dias parados · assinou em 0
--     Beatriz   8 dias parados · assinou em 0
--     Kelma     3 dias parados · assinou em 0
--     Bianca    1 dia  parado  · assinou em 0
--     Renata    0            · assinou em 1
--     Claudia   0            · assinou em 0
-- O print que motivou mostrava a Josiany como "esperando assinatura há 23 dias".
-- O contrato dela foi enviado em 14/09 e ela assinou no mesmo dia: ninguém
-- esperava ela — ela é que esperava o contrato.
--
-- O CUIDADO COM `contratos.status`: a coluna nasce 'enviado' por padrão e MENTE.
-- O contrato nº 8 da conta 34 está `status='enviado'` com `enviado_em` NULO,
-- nunca mandado. Quem manda é a data. É a mesma leitura que
-- `vendas.linha_do_funil` já fazia (`contrato_enviado_em`); o Raio-X é que não.
--
-- O PORTÃO: `servico`. O bloco é de orçamento e contrato, do módulo Serviços.
-- Dentro dele, "parado em casa" só aparece onde existe contrato pra assinar —
-- `raio_x_perfil.perfil()["contrato"]`, derivado de `contrato.tem_contrato` (§6).
-- No recorrente não há documento, e o número não teria sentido.
--
-- PRA QUEM: dono e gestor. O bloco por vendedor é da tela do painel, que pede a
-- capacidade `financeiro` — o vendedor não a tem.
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('raio-x-bola-e-parado-em-casa', 'mudanca', 'servico', '{dono,gestor}',
 'O Raio-X agora diz de quem é a bola no contrato',
 'O bloco de contratos aguardando assinatura foi separado em dois — o que depende de você mandar e o que depende do cliente assinar — e ganhou a medida de quanto o contrato fica pronto esperando envio.',
 '/painel/raio-x',
 $txt$O bloco "Aprovado, esperando assinatura" juntava duas situações opostas sob o mesmo nome: contrato que o cliente recebeu e ainda não assinou, e contrato que nunca saiu daqui. São problemas de donos diferentes, e um deles se resolve em um clique.

AGORA SÃO DOIS GRUPOS.

"A bola está com você" — o contrato está pronto e não foi enviado. Ninguém está esperando o cliente; o cliente é que está esperando.

"A bola está com o cliente" — ele recebeu e ainda não assinou. Aí sim cabe cobrar.

E CADA LINHA MOSTRA OS DOIS DOCUMENTOS, com data e por quem: o orçamento aprovado e o contrato, com o número dele e em que pé está. Antes a linha dizia só "há N dias", sem dizer de qual coisa.

O RELÓGIO FOI CORRIGIDO. "Esperando assinatura há 23 dias" contava desde a aprovação do orçamento — então um contrato enviado ontem aparecia com 23 dias de espera. Agora conta do envio. O que ainda não foi enviado ganha o próprio relógio, com o nome certo: "pronto há N dias — nunca enviado".

A MEDIDA NOVA: PARADO EM CASA. No topo, junto dos contratos, aparece quantos dias o contrato costuma ficar pronto aqui dentro antes de ir pro cliente, e quantos estão parados neste momento. É o número que faltava: medindo os contratos já assinados, o tempo somado esperando o cliente é quase zero — as pessoas assinam no mesmo dia em que recebem. O tempo que se perde é o de antes do envio, e ele não aparecia em lugar nenhum.

Quem vende por mensalidade não vê essa medida: ali não existe contrato pra assinar, e o número não teria sentido.$txt$,
 timestamptz '2026-09-14 23:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'raio-x-bola-e-parado-em-casa';
