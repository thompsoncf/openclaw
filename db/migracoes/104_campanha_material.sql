-- 104_campanha_material.sql
-- Material da campanha: o link/texto que é enviado automaticamente quando o lead
-- clica "Tenho interesse" no e-mail (CTA). Ex.: link de apresentação, PDF, vídeo.
-- Idempotente.

alter table public.campanhas add column if not exists material text;
