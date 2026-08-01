-- 115_campanha_wa_sid.sql
-- SID da mensagem de WhatsApp enviada pelo Twilio (SM...), pra casar os
-- StatusCallback (entregue/lido/falhou) com o lead da campanha. Idempotente.

alter table public.campanha_alvos add column if not exists wa_sid text;
create index if not exists idx_alvos_wa_sid on public.campanha_alvos (wa_sid);
