-- 145_canal_templates_agenda.sql
-- Templates da AGENDA por empresa (antes só existiam como env global no Render:
-- TWILIO_TMPL_CONVITE_SID / TWILIO_TMPL_LEMBRETE_SID).
--
-- Env global não serve multi-tenant: o template do WhatsApp é aprovado dentro da
-- conta vinculada ao NÚMERO, e cada empresa tem o seu (canais_config.identificador
-- — ver whatsapp_out.enviar_template, que se recusa a cair num número global pra
-- não mandar com a identidade de outra empresa). Um SID aprovado no número A não
-- vale no número B. Mesmo caminho que a campanha (111) e a distribuição (133) já
-- fizeram; a agenda era a última só na env.
--
-- Guardado aqui e não em agenda_config porque o template pertence ao NÚMERO, que
-- mora nesta tabela junto de provedor/wa_phone_id/token. Dual-uso igual à 133:
--   * Twilio   → Content SID (HX...)
--   * Cloud API → o NOME do template aprovado na Meta
-- Vazio = cai na env global (fallback), e sem env nenhuma o disparo automático
-- fica desligado — o convite continua indo pelo link manual. Idempotente.

alter table public.canais_config add column if not exists tmpl_convite_sid text;
alter table public.canais_config add column if not exists tmpl_lembrete_sid text;
