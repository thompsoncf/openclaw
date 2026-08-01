-- 116_mensagens_status.sql
-- Status de entrega por MENSAGEM (WhatsApp via Twilio): enviado/entregue/lido/erro.
-- Casado pelo provider_sid no StatusCallback. Aparece como ✓/✓✓/👀 no inbox.
-- Idempotente.

alter table public.mensagens add column if not exists status text;
