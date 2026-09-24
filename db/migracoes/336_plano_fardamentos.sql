-- 336_plano_fardamentos.sql
-- 4.1.06 Fardamentos, em Despesas com Pessoal.
--
-- Pedido do dono em 24/09/2026, pra Prime Eventos: "cria uma conta FARDAMENTOS no
-- plano de contas, dentro de despesa com pessoal". Farda da equipe do evento
-- (garçons, recepção, cozinha) é gasto com a PESSOA que trabalha, não material de
-- operação — por isso fica no grupo 4, ao lado de salários e benefícios, e não em
-- Despesas Operacionais.
--
-- POR QUE ENTRA PRA TODO MUNDO
-- O plano de contas é GLOBAL (finance/plano_contas.py): uma árvore só, e cada
-- empresa liga ou desliga o que usa. Não existe conta de uma empresa só. Como a
-- ausência de linha em `plano_conta_habilitada` quer dizer habilitada, a 4.1.06
-- aparece ligada em toda empresa — é o mesmo caminho da 143 (Diaristas) e da 186
-- (Aporte de Sócios). Farda é gasto comum a qualquer negócio com equipe; quem não
-- usa, desliga no plano de contas.
--
-- Conferido em produção antes (só leitura): não existia conta com "fard" ou
-- "uniform" no nome, nem lançamento da Prime com essas palavras pra reclassificar.
--
-- Aditiva e idempotente. Nada existente muda de valor, só a `ordem`, que é
-- recalculada pelo código (mesma regra da 143): a 4.1.06 cai logo depois da
-- 4.1.05 e o grupo 5 anda uma posição.

insert into public.plano_contas (codigo, nome, grupo, natureza, ordem) values
  ('4.1.06', 'Fardamentos', 4, 'despesa', 20)
on conflict (codigo) do nothing;

with seq as (
    select id, row_number() over (order by codigo) as n from public.plano_contas
)
update public.plano_contas p
   set ordem = seq.n::smallint
  from seq
 where seq.id = p.id and p.ordem <> seq.n;

-- rollback (manual):
--   delete from public.plano_contas where codigo = '4.1.06';
--   (antes, conferir que nenhum lançamento/título aponta pra ela; a ordem volta
--    sozinha no próximo recálculo)
