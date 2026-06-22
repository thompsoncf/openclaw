-- Migração 040 — guardar o subscription_id do Asaas na assinatura (Fase 5)
alter table assinaturas add column if not exists asaas_subscription_id text;

-- ROLLBACK: alter table assinaturas drop column if exists asaas_subscription_id;
