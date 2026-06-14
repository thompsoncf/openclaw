-- 012_precos_fonte.sql
-- Rastreia a origem dos preços: cupom (do cliente), sefaz (API), historico, etc
-- Permite separar preços por fonte e prioridade na busca

alter table precos_observados add column if not exists fonte text not null default 'cupom';
create index if not exists idx_precos_fonte on precos_observados(fonte);
