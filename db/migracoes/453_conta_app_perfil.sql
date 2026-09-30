-- 453_conta_app_perfil.sql
-- PERFIL DO APP por conta (pedido do dono, 30/09/2026): o Outlet Chic (CNPJ
-- 30.961.685/0001-01) deixa de herdar o app de festa da Prime. `app_perfil` NULL =
-- o app de sempre (Prime e todas as outras contas); 'stands' = o app de venda de
-- estandes (abre no mapa, abas Stands / Minhas vendas / Perfil).
--
-- A marca é gravada SÓ na conta do Outlet Chic, identificada pelo CNPJ (não pelo
-- id), junto com o nome fantasia que o contrato mostra na locadora. Idempotente:
-- só age enquanto `app_perfil` estiver vazio.
alter table public.contas add column if not exists app_perfil text;

update public.contas
   set app_perfil = 'stands', nome_fantasia = 'OUTLET CHIC'
 where regexp_replace(coalesce(documento, ''), '[^0-9]', '', 'g') = '30961685000101'
   and app_perfil is null;

-- rollback:
--   alter table public.contas drop column if exists app_perfil;
