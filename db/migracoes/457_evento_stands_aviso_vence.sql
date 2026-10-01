-- 457_evento_stands_aviso_vence.sql
-- Quando o vendedor foi avisado de que a pré-reserva do estande está pra vencer
-- (aviso único por reserva; uma reserva nova zera a marca). Aditiva e idempotente.
alter table public.evento_stands add column if not exists aviso_vence_em timestamptz;

-- rollback:
--   alter table public.evento_stands drop column if exists aviso_vence_em;
