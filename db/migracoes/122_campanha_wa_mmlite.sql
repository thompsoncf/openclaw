-- 122_campanha_wa_mmlite.sql
-- Roteia o disparo de marketing da campanha pela Marketing Messages Lite API da Meta
-- (endpoint /marketing_messages) em vez do Cloud API comum (/messages). MESMO preço e
-- MESMO template — a MM Lite só otimiza entrega/leitura. Só vale no provedor 'cloud' e
-- exige a WABA habilitada em MM Lite na Meta; default desligado.

alter table public.campanhas add column if not exists wa_mmlite boolean default false;
