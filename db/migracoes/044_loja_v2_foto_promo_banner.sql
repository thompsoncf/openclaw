-- ============================================================
-- Migração 044 — Loja v2: foto, descrição, promoção, banner
-- ============================================================
-- Adiciona o que o redesign da loja precisa.
-- Tudo opcional (nullable / default), não quebra nada existente. Idempotente.
-- ============================================================

-- produtos: foto + descrição curta
alter table catalogo_produtos add column if not exists foto_url text;
alter table catalogo_produtos add column if not exists descricao_curta text;

-- produtos: promoção
alter table catalogo_produtos add column if not exists preco_promo_centavos bigint;
alter table catalogo_produtos add column if not exists em_promo boolean not null default false;

-- fornecedor: banner do cabeçalho da loja
alter table contas add column if not exists banner_url text;
alter table contas add column if not exists banner_cor text;

-- índice pra listar promoções rápido
create index if not exists ix_catalogo_promo on catalogo_produtos (fornecedor_id, em_promo)
  where ativo and em_promo;

-- ============================================================
-- ROLLBACK:
--   alter table catalogo_produtos drop column if exists foto_url;
--   alter table catalogo_produtos drop column if exists descricao_curta;
--   alter table catalogo_produtos drop column if exists preco_promo_centavos;
--   alter table catalogo_produtos drop column if exists em_promo;
--   alter table contas drop column if exists banner_url;
--   alter table contas drop column if exists banner_cor;
--   drop index if exists ix_catalogo_promo;
-- ============================================================
