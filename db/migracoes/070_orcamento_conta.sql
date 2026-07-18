-- 070_orcamento_conta.sql
-- Multi-tenant do módulo Vendas de Serviços: o orçamento pertence a uma EMPRESA
-- (conta), não é mais global do admin. Cada empresa de tecnologia vê e fecha os
-- SEUS orçamentos, no painel dela. add-if-not-exists: idempotente.
alter table orcamentos add column if not exists conta_id bigint references contas(id) on delete restrict;
create index if not exists idx_orcamentos_conta on orcamentos (conta_id, status, criado_em desc);

-- Backfill: os orçamentos históricos (criados quando a tela era global no admin)
-- ficam com a conta da empresa (Aladdin = conta 3 nesta instância).
update orcamentos set conta_id = 3 where conta_id is null;
