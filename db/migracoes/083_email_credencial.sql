-- 083_email_credencial.sql
-- Credencial IMAP por conta (configurável na aba Canais), pra não depender só do
-- env global. imap_senha = senha de app do provedor; imap_host = servidor de entrada.
-- Idempotente.

alter table public.canais_config add column if not exists imap_senha text;
alter table public.canais_config add column if not exists imap_host text;
