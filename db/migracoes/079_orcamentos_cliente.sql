-- 079_orcamentos_cliente.sql
-- Mais dados do cliente no orçamento, herdados da prospecção ao "Gerar orçamento"
-- (telefone separado do WhatsApp, cidade/UF, site, cargo do contato e sócio).
-- Antes a conversão jogava fora esses campos. Tudo text e idempotente.

alter table public.orcamentos add column if not exists telefone text;
alter table public.orcamentos add column if not exists cidade   text;
alter table public.orcamentos add column if not exists uf       text;
alter table public.orcamentos add column if not exists site     text;
alter table public.orcamentos add column if not exists cargo    text;
alter table public.orcamentos add column if not exists socio    text;
