-- 082_email_inbound.sql
-- Suporte a e-mail RECEBIDO (IMAP) no inbox omnichannel. Idempotente.
--   1) canais_config passa a aceitar canal 'email' (a caixa da empresa) e ganha um
--      checkpoint de UID (ultimo_uid) pra puxar só o que é novo.
--   2) índice único parcial em mensagens.provider_sid: cada Message-ID/SID entra uma
--      vez só — protege contra corrida entre os 2 workers ao importar o mesmo e-mail.

alter table public.canais_config drop constraint if exists canais_config_canal_check;
alter table public.canais_config
  add constraint canais_config_canal_check
  check (canal in ('whatsapp','messenger','instagram','email'));

alter table public.canais_config add column if not exists ultimo_uid bigint;

create unique index if not exists idx_mensagens_provider_sid
  on public.mensagens (provider_sid) where provider_sid is not null;
