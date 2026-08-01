-- 114_campanha_abertura.sql
-- Acompanhamento de ABERTURA de e-mail por lead (pixel de rastreio). SMTP não dá
-- webhook de abertura, então a gente mede com um pixel 1x1 no corpo do e-mail:
--   aberto_em  — 1ª vez que o e-mail foi aberto (carregou o pixel).
--   aberturas  — quantas vezes carregou (contagem).
-- Idempotente.

alter table public.campanha_alvos add column if not exists aberto_em timestamptz;
alter table public.campanha_alvos add column if not exists aberturas int not null default 0;
