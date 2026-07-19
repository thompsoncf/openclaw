-- 078_prospeccao_site.sql
-- Guarda o link do site do lead (o Google Places traz a URL; antes só o booleano
-- tem_site). Assim o vendedor abre a página pra avaliar e usar como gancho de
-- venda. Idempotente.

alter table public.prospeccao add column if not exists site_url text;
