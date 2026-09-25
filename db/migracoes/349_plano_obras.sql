-- 349_plano_obras.sql
-- Duas contas novas no plano de contas, pra construção e reforma:
--   1.1.04 Venda de Imóveis     (Receita Operacional Bruta)
--   3.1.04 Mão de Obra de Obras (Custos — CMV / CSP)
--
-- Decisão do dono em 25/09/2026 (docs/mockups/nicho_construcao.html, decisão 4).
-- A PX2 (conta 33) constrói casa popular pra vender e faz reforma, e o DRE dela
-- não tinha onde pôr nenhuma das duas coisas que decidem se a casa dá lucro:
--
-- * A CASA VENDIDA não é "Venda de Produtos" (1.1.01, que é mercadoria de
--   prateleira) nem "Prestação de Serviços" (1.1.02, que é onde a REFORMA dela
--   fica). Misturar as duas apagaria a diferença entre o negócio de casa e o de
--   reforma no resultado.
-- * A MÃO DE OBRA DA OBRA — empreiteiro, pedreiro, diarista, subempreiteiro — é
--   custo da casa, ao lado do material (3.1.03 Insumos e Materiais). Em "Serviços
--   Terceirizados" (5.1.10) ela cairia em despesa operacional, e o custo da casa
--   sairia menor do que é. No uso da PX2, medido em 25/09 (só leitura): R$ 11.500
--   de mão de obra em 2 lançamentos, contra R$ 10.480 de material e louça.
--
-- POR QUE ENTRA PRA TODO MUNDO: o plano é GLOBAL (finance/plano_contas.py), e a
-- ausência de linha em `plano_conta_habilitada` quer dizer habilitada. Mesmo
-- caminho da 143 (Diaristas), da 186 (Aporte de Sócios) e da 336 (Fardamentos):
-- quem não usa, desliga no plano de contas.
--
-- Aditiva e idempotente. Nada existente muda de valor, só a `ordem`, recalculada
-- pelo código (mesma regra da 143 e da 336).

insert into public.plano_contas (codigo, nome, grupo, natureza, ordem) values
  ('1.1.04', 'Venda de Imóveis',     1, 'receita', 4),
  ('3.1.04', 'Mão de Obra de Obras', 3, 'despesa', 14)
on conflict (codigo) do nothing;

with seq as (
    select id, row_number() over (order by codigo) as n from public.plano_contas
)
update public.plano_contas p
   set ordem = seq.n::smallint
  from seq
 where seq.id = p.id and p.ordem <> seq.n;

-- rollback (manual):
--   delete from public.plano_contas where codigo in ('1.1.04', '3.1.04');
--   (antes, conferir que nenhum lançamento/título aponta pra elas; a ordem volta
--    sozinha no próximo recálculo)
