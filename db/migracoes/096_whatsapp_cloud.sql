-- 096_whatsapp_cloud.sql
-- WhatsApp do PRÓPRIO cliente, sem BSP/Twilio. Duas formas de conectar o número:
--   * 'cloud' = WhatsApp Cloud API oficial da Meta (número migra pro sistema; grátis,
--     sem risco de ban). Reaproveita o mesmo webhook /webhooks/meta (assinatura HMAC).
--   * 'qr'    = sessão tipo WhatsApp Web (usa o número como está; exige serviço Node
--     à parte sempre-ligado; contra os termos → risco de ban). Reservado pra futuro.
-- 'twilio' segue o padrão (default) pra não mexer em quem já usa.
-- Idempotente.

alter table public.canais_config
  add column if not exists provedor text not null default 'twilio';
alter table public.canais_config drop constraint if exists canais_config_provedor_check;
alter table public.canais_config
  add constraint canais_config_provedor_check
  check (provedor in ('twilio','cloud','qr'));

-- phone_number_id da Cloud API (id do número na Meta; roteia o inbound e monta a URL
-- de envio /{wa_phone_id}/messages). O access token vai na coluna `token` (já existe).
alter table public.canais_config add column if not exists wa_phone_id text;
