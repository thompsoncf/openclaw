-- 137_membro_comissao_pct.sql
-- Comissão por vendedor: cada membro da equipe tem seu próprio percentual de
-- comissão sobre as vendas que lançou no período — usado pelo relatório de
-- Comissão (módulo Relatórios). Aditivo e idempotente; nulo/0 = sem comissão
-- configurada ainda (mesmo padrão de contas.comissao_pct, migração 031).

alter table membros add column if not exists comissao_pct numeric(5,2);  -- ex: 5.00 = 5%
