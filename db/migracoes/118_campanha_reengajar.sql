-- 118_campanha_reengajar.sql
-- Follow-up automático (reengajamento multicanal): quem recebeu a sequência e não
-- respondeu em N dias leva 1 toque pelo OUTRO canal (WhatsApp se ativo, senão um
-- e-mail curto de reforço). Opt-in por campanha, dispara UMA vez por lead.
--   reengajar_ativo : liga/desliga por campanha
--   reengajar_dias  : dias de silêncio antes do follow-up (default 3)
--   reengajado_em   : marca no alvo que o follow-up já saiu (não repete)
-- Idempotente.

alter table public.campanhas add column if not exists reengajar_ativo boolean not null default false;
alter table public.campanhas add column if not exists reengajar_dias  int     not null default 3;
alter table public.campanha_alvos add column if not exists reengajado_em timestamptz;
