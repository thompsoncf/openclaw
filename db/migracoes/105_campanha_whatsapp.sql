-- 105_campanha_whatsapp.sql
-- Disparo automático de 1º contato por WhatsApp (template aprovado) na campanha,
-- em PARALELO ao e-mail. Canal-agnóstico: a orquestração é a mesma pro Twilio e,
-- depois, pro Cloud API. Idempotente.

-- Config por campanha
alter table public.campanhas add column if not exists wa_ativo boolean default false;
alter table public.campanhas add column if not exists limite_wa_dia int default 30;
alter table public.campanhas add column if not exists wa_enviados_hoje int default 0;
alter table public.campanhas add column if not exists wa_dia_contagem date;

-- Estado do envio de WhatsApp por lead (dedup: não reenvia)
--   null        = ainda não tentou
--   'enviado'   = template disparado
--   'erro'      = falhou no envio
--   'sem_numero'= lead sem número usável
alter table public.campanha_alvos add column if not exists wa_status text;
alter table public.campanha_alvos add column if not exists wa_em timestamptz;
