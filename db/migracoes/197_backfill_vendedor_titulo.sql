-- 197_backfill_vendedor_titulo.sql
-- Preenche o vendedor que ficou faltando em título e lançamento — sem ele, o
-- relatório de Vendas mostra "-" onde deveria mostrar o nome de quem fechou.
--
-- Achado em 04/09/2026, com dado real de produção: dois orçamentos (Claudia
-- Maria Almeida de Carvalho e Bianca Oliveira) têm vendedor gravado
-- (`orcamentos.criado_por`), mas os 5 títulos que nasceram deles — sinal e
-- parcelas — foram criados com `criado_por` NULO. A causa é de código
-- (`fechar_orcamento` / `lancar_sinal_recebido` não repassavam o vendedor do
-- orçamento pro título) e está corrigida em `finance/vendas.py` nesta mesma
-- leva; esta migração só recupera o que já tinha acontecido antes do conserto.
--
-- O dado não é chute: `orcamentos.criado_por` já guarda o id do membro (como
-- texto) desde a migração 068, e é a MESMA fonte que o relatório de Orçamentos
-- já usa pra mostrar o vendedor lá. Só título 'pago' com contraparte NUMÉRICA
-- (nunca 'dono', que é conta sem vendedor específico) entra — e só o membro
-- daquela conta, pra não colar id de outra empresa por coincidência de número.
--
-- Aditiva e idempotente: só toca `criado_por`/`membro_id` que estão NULOS;
-- rodar de novo depois de aplicada não muda nada.

update titulos t
   set criado_por = o.criado_por::bigint
  from orcamentos o
 where t.orcamento_id = o.id
   and t.conta_id = o.conta_id
   and t.criado_por is null
   and o.criado_por ~ '^[0-9]+$'
   and exists (select 1 from membros m
                where m.id = o.criado_por::bigint and m.conta_id = t.conta_id);

update lancamentos l
   set membro_id = t.criado_por
  from titulos t
 where l.id = t.lancamento_id
   and l.conta_id = t.conta_id
   and l.membro_id is null
   and t.criado_por is not null;
