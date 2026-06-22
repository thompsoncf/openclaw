-- ============================================================
-- Migração 039 — Plano "Zaq Cesta" (assinante de cesta)
-- ============================================================
-- Cria um plano nomeado pro assinante de cesta: identidade + filtrável +
-- contável (quantos só-cesta existem = base do upsell). Grátis (preço 0).
-- ativo=false: NÃO aparece na lista de escolha do cadastro do app — só é
-- atribuído quando a pessoa entra pela LOJA. Idempotente.
-- ============================================================

insert into planos (codigo, nome, tipo_conta, preco_base_centavos, membros_inclusos, preco_assento_centavos, ativo)
values ('zaq_cesta', 'Zaq Cesta', 'pf', 0, 1, 0, false)
on conflict (codigo) do nothing;

-- (limites por dia ficam em contas, não no plano: o cadastro grava
--  limite_mensagens_dia=20 e limite_cupons_dia=0 pro assinante de cesta.)

-- ============================================================
-- ROLLBACK:
--   delete from planos where codigo = 'zaq_cesta';
-- ============================================================
