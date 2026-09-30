-- 453_plano_despesas_diversas.sql
-- 5.1.13 Despesas Diversas, em Despesas Operacionais.
--
-- Pedido do dono em 30/09/2026: "uma conta DESPESAS DIVERSAS no plano de contas
-- dentro de Despesas operacionais". Conferido antes (só leitura no repositório):
-- não existia conta "diversas" nem "outras despesas" em nenhuma migração.
--
-- POR QUE ENTRA PRA TODO MUNDO
-- O plano de contas é GLOBAL (finance/plano_contas.py): uma árvore só, cada empresa
-- liga ou desliga o que usa, e ausência de linha em `plano_conta_habilitada` quer
-- dizer habilitada. Mesmo caminho da 143, 186, 336 e 351. Todo negócio tem gasto
-- pequeno que não cabe em nenhuma das 12 contas de operação.
--
-- A FRONTEIRA
-- É a conta do que NÃO tem conta própria. Se o gasto cabe em Aluguel, Utilidades,
-- Marketing, Escritório, Limpeza, Software, Contabilidade, Viagens, Diaristas,
-- Terceirizados, Manutenção ou Materiais e Utensílios, vai na específica; "diversas"
-- que vira depósito esconde o gasto da DRE. Custo (grupo 3) nunca vem pra cá.
--
-- Nada é reclassificado (regra 0 do CLAUDE.md). Aditiva e idempotente; a `ordem` é
-- recalculada por `order by codigo`, como na 143 e na 351.

insert into public.plano_contas (codigo, nome, grupo, natureza, ordem) values
  ('5.1.13', 'Despesas Diversas', 5, 'despesa', 34)
on conflict (codigo) do nothing;

with seq as (
    select id, row_number() over (order by codigo) as n from public.plano_contas
)
update public.plano_contas p
   set ordem = seq.n::smallint
  from seq
 where seq.id = p.id and p.ordem <> seq.n;

-- rollback (manual):
--   delete from public.plano_contas where codigo = '5.1.13';
--   (antes, conferir que nenhum lançamento/título aponta pra ela)
