-- 076_prospeccao_maps.sql
-- Guarda o link do Google Maps do alvo numa coluna própria (antes ia na obs e
-- poluía o layout). Idempotente.

alter table public.prospeccao add column if not exists maps_url text;
