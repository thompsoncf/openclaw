-- ============================================================
-- Migração 037 — Flag de assinante de cesta (separar do app pago)
-- ============================================================
-- O cliente que vem pela LOJA do fornecedor só quer a cesta. Ele NÃO
-- é obrigado a assinar plano do app financeiro. Esta flag marca quem
-- entrou só pela cesta — o painel dele mostra só a assinatura, e o
-- plano do app fica como convite (upsell), nunca obrigação. Idempotente.
-- ============================================================

alter table contas add column if not exists eh_assinante_cesta boolean not null default false;

-- quem é assinante de cesta e não tem plano do app entra com plano nulo/grátis.
-- (o campo 'plano' já existe; pra esses, fica null ou 'cesta'.)

-- ============================================================
-- ROLLBACK:
--   alter table contas drop column if exists eh_assinante_cesta;
-- ============================================================
