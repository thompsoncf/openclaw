-- 202610031459_novidade_inventario_do_cd.sql
-- O QUE FAZ: o aviso do inventário rotativo do CD (finance/obra_inventario.py),
--   pela seção 5 do CLAUDE.md. Portão `construcao` (350); dono e gestor (199).
-- POR QUÊ: PR 3b do CD das obras, aprovado pelo dono em 03/10/2026.
--
-- Aditiva e idempotente (on conflict do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('obras-inventario-cd', 'novidade', 'construcao', '{dono,gestor}',
 'Inventário sem parar o depósito: 5 itens por dia',
 'O depósito agora diz quais materiais contar hoje — os que mais pesam no bolso primeiro (curva ABC). A diferença vira ajuste com motivo, e você vê a acuracidade, as perdas e o giro.',
 '/painel/obras/deposito?aba=inventario',
 $txt$Contar o depósito inteiro de uma vez para tudo, e por isso ninguém faz. Contar pouco todo dia, sim.

A CONTAGEM DO DIA

Em Depósito (CD) › Inventário, o Zaq separa até 5 materiais por dia, começando pelos da classe A — os poucos que são a maior parte do dinheiro (cimento, ferro, telha). Conte, digite, pronto.

A CURVA ABC

Calculada sozinha, pelo valor que saiu do depósito nos últimos 90 dias. A classe A é contada toda semana, a B a cada 15 dias, a C uma vez por mês. Aparece também na aba Estoque.

QUANDO NÃO BATE

A diferença vira ajuste no estoque, com o motivo: quebra, perda, furto, erro de lançamento ou achou a mais. Errou a contagem? Dá pra desfazer no mesmo dia.

COMO O DEPÓSITO ESTÁ INDO

Acuracidade (quantas contagens bateram), perdas do mês, notas de fornecedor com diferença, quanto tempo o pedido da obra leva pra chegar e o giro do material que mais pesa.$txt$,
 timestamptz '2026-10-04 00:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'obras-inventario-cd';
