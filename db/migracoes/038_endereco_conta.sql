-- ============================================================
-- Migração 038 — Coluna de endereço na conta (entrega da cesta)
-- ============================================================
-- O assinante de cesta precisa de endereço pra ENTREGA. A conta hoje
-- guarda só a cidade (via CEP). Adiciona o endereço completo. Idempotente.
-- ============================================================

alter table contas add column if not exists endereco text;
alter table contas add column if not exists cep text;

-- ============================================================
-- ROLLBACK:
--   alter table contas drop column if exists endereco;
--   alter table contas drop column if exists cep;
-- ============================================================
