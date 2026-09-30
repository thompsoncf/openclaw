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

-- dentro de DO e com a checagem das colunas: a migração também roda em bancos
-- mínimos (teste de blindagem) onde `contas` não tem documento/nome_fantasia.
do $$
begin
    if exists (select 1 from information_schema.columns
                where table_schema = 'public' and table_name = 'contas' and column_name = 'documento')
       and exists (select 1 from information_schema.columns
                    where table_schema = 'public' and table_name = 'contas' and column_name = 'nome_fantasia')
    then
        execute $q$
            update public.contas
               set app_perfil = 'stands', nome_fantasia = 'OUTLET CHIC'
             where regexp_replace(coalesce(documento, ''), '[^0-9]', '', 'g') = '30961685000101'
               and app_perfil is null
        $q$;
    end if;
end $$;

-- rollback:
--   alter table public.contas drop column if exists app_perfil;
