-- 120_campanha_wa_erro.sql
-- Guarda o código/mensagem de erro real do provedor (Twilio) quando um envio de
-- WhatsApp de campanha falha. Antes só gravávamos wa_status='erro' genérico, sem
-- saber se foi "número sem WhatsApp", token inválido, rate limit etc.

alter table public.campanha_alvos add column if not exists wa_erro_codigo text;
alter table public.campanha_alvos add column if not exists wa_erro_msg text;
