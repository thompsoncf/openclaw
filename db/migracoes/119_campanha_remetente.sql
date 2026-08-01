-- 119_campanha_remetente.sql
-- Duas caixas de e-mail por conta + remetente escolhido por campanha.
--   canais_config.canal ganha 'email2' (a caixa secundária; a principal é 'email').
--   campanhas.remetente_slot: 'principal' (caixa 'email') | 'secundario' (caixa 'email2').
-- Idempotente.

alter table public.canais_config drop constraint if exists canais_config_canal_check;
alter table public.canais_config
  add constraint canais_config_canal_check
  check (canal in ('whatsapp','messenger','instagram','email','email2'));

alter table public.campanhas add column if not exists remetente_slot text not null default 'principal';
