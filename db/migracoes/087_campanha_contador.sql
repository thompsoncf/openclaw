-- 087_campanha_contador.sql
-- Contador diário de envios por campanha (pro limite/dia do motor de disparo).
-- Idempotente.

alter table public.campanhas add column if not exists dia_contagem date;
alter table public.campanhas add column if not exists enviados_hoje int not null default 0;
