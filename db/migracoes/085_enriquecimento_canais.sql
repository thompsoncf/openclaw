-- 085_enriquecimento_canais.sql
-- Enriquecimento/verificação de canais do lead: guarda se o e-mail foi validado e
-- quando o lead foi enriquecido (raspagem do site → e-mail/Instagram/WhatsApp).
-- (instagram/whatsapp/email/site_url já existem em prospeccao.) Idempotente.

alter table public.prospeccao add column if not exists email_ok boolean;
alter table public.prospeccao add column if not exists enriquecido_em timestamptz;
