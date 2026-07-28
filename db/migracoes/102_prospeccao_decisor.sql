-- 102_prospeccao_decisor.sql
-- Decisor do lead (sócio-administrador) descoberto via Credify — guardado em
-- colunas DEDICADAS pra não sobrescrever o contato/sócio que o vendedor digitou.
-- decisor_telefone/whatsapp podem ficar nulos se a conta Credify não tiver a
-- consulta de telefone liberada (só o nome+cargo entram). Idempotente.

alter table public.prospeccao add column if not exists decisor_nome      text;
alter table public.prospeccao add column if not exists decisor_cargo     text;
alter table public.prospeccao add column if not exists decisor_telefone  text;
alter table public.prospeccao add column if not exists decisor_whatsapp  boolean;
alter table public.prospeccao add column if not exists decisor_em        timestamptz;
