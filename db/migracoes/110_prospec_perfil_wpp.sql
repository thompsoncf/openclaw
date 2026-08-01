-- 110_prospec_perfil_wpp.sql
-- Perfil de prospecção da conta: personaliza o 1º contato (template de WhatsApp)
-- sem "chumbar" nada global no template compartilhado. Cada conta preenche o seu.
--   prospec_instagram — @ ou URL do Instagram (referência que o lead recebe ao
--                       tocar "Quero te conhecer").
--   prospec_cargo     — cargo de quem envia (ex.: CEO, fundador, dono). Vai no
--                       corpo do convite. Default 'CEO'.
-- Idempotente.

alter table public.contas add column if not exists prospec_instagram text;
alter table public.contas add column if not exists prospec_cargo     text default 'CEO';
