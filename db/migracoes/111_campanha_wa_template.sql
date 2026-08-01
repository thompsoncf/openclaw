-- 111_campanha_wa_template.sql
-- Content SID do template de WhatsApp POR CAMPANHA. Antes o SID vinha só da env
-- global TWILIO_TMPL_PROSPEC_SID (um só, e mexer no Render toda vez). Agora cada
-- campanha guarda o seu template aprovado, setado na própria tela da campanha; a
-- env continua valendo como fallback (ex.: convite 1-a-1 da ficha). Idempotente.

alter table public.campanhas add column if not exists wa_template_sid text;
