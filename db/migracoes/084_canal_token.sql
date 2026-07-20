-- 084_canal_token.sql
-- Token de acesso por canal/conta (ex.: Page Access Token da Meta pra Messenger/
-- Instagram). As credenciais de APP (META_APP_SECRET/META_VERIFY_TOKEN) são globais
-- (env); o token de página é por empresa. Idempotente.

alter table public.canais_config add column if not exists token text;
