-- ============================================================================
--  APLICADO em 19/09/2026, à mão, no SQL Editor. Conferido depois pelo
--  conector somente-leitura: anon=0 e authenticated=0 nas 124 tabelas (inclusive
--  TRUNCATE); postgres e service_role seguem com 124; sequences públicas = 0;
--  default privilege do `postgres` agora só dá acesso a postgres e service_role;
--  RLS continua ligada nas 124.
--  Sobram 4 funções com EXECUTE pra PUBLIC (rls_auto_enable e as três trava_*).
--  São funções de TRIGGER/event trigger: o Postgres não deixa chamá-las direto
--  e a API não as expõe. Inofensivas — deixadas como estão de propósito.
-- ============================================================================
--  PROPOSTA — NÃO É MIGRAÇÃO. NÃO RODA SOZINHA.
--
--  Mora em db/propostas/, fora de db/migracoes/: o preDeployCommand do Render
--  só lê db/migracoes/, então este arquivo NUNCA entra num deploy por conta
--  própria. Quem executa é uma pessoa, à mão, no SQL Editor do Supabase.
--
--  O QUE FAZ: tira dos papéis PÚBLICOS do Supabase (anon e authenticated) as
--  permissões nas tabelas do schema public.
--
--  O QUE NÃO FAZ:
--    - não altera NENHUMA linha de dado (nenhum insert/update/delete)
--    - não apaga NADA (nenhum drop, nenhum truncate)
--    - não toca em NENHUMA chave (anon key, service_role, senha — nada)
--    - não mexe na RLS (continua ligada, como está)
--    - não afeta o app: ele conecta como `postgres`, que não é tocado
--    - não afeta o Storage (uploads): ele usa `service_role`, que não é tocado
--  Muda só PERMISSÕES. É reversível com o bloco ROLLBACK no fim.
--
-- ----------------------------------------------------------------------------
--  POR QUE (medido no banco em 19/09/2026, conector somente-leitura)
-- ----------------------------------------------------------------------------
--  1. `anon` — qualquer pessoa na internet com a URL do projeto e a chave
--     pública — tem SELECT, INSERT, UPDATE e DELETE nas 124 tabelas. Idem
--     `authenticated`. São os privilégios-padrão do Supabase.
--  2. A API REST está NO AR servindo as 124 tabelas (log do PostgREST:
--     "Schema cache loaded 124 Relations").
--  3. A ÚNICA barreira hoje é a RLS ligada sem policy (nega tudo pra esses
--     papéis). Funciona — mas é uma camada só. Se a RLS for desligada em UMA
--     tabela, por engano ou por uma migração, essa tabela fica aberta pra
--     leitura E escrita E exclusão pela internet.
--  4. NADA usa esses papéis:
--       - código: openclaw fala com o Postgres direto (`postgres`) e com o
--         Storage via `service_role`; nenhum uso de /rest/v1 nem da anon key.
--         zaq-landing não usa Supabase. gestora-capital é OUTRO projeto.
--       - logs das últimas 24h: ZERO requisição a /rest/v1/<tabela>. O tráfego
--         é só health check interno (/rest-admin/v1/ready, /auth/v1/health) e
--         Storage. Os 371 eventos do PostgREST são internos (reload de cache).
--       - Realtime: nenhuma tabela publicada.
--  Resultado: revogar remove superfície de ataque SEM custo funcional, e
--  passa a haver DUAS camadas (permissão + RLS) em vez de uma.
--
-- ----------------------------------------------------------------------------
--  COMO RODAR — nesta ordem, no SQL Editor do Supabase
-- ----------------------------------------------------------------------------
--  Passo 0 e Passo 3 são só leitura. O Passo 1+2 é o único que muda algo.
-- ============================================================================


-- ============================================================================
-- PASSO 0 — FOTO DO ANTES (só leitura). Guarde o resultado.
-- Esperado hoje: anon e authenticated com 124 em tudo.
-- ============================================================================
select r.rolname                                                             as papel,
       count(*) filter (where has_table_privilege(r.rolname, c.oid, 'SELECT')) as select_,
       count(*) filter (where has_table_privilege(r.rolname, c.oid, 'INSERT')) as insert_,
       count(*) filter (where has_table_privilege(r.rolname, c.oid, 'UPDATE')) as update_,
       count(*) filter (where has_table_privilege(r.rolname, c.oid, 'DELETE')) as delete_,
       count(*)                                                               as tabelas
  from pg_roles r
 cross join pg_class c
  join pg_namespace n on n.oid = c.relnamespace and n.nspname = 'public'
 where r.rolname in ('anon', 'authenticated', 'service_role')
   and c.relkind = 'r'
 group by r.rolname
 order by r.rolname;


-- ============================================================================
-- PASSO 1+2 — O REVOKE. Numa transação: ou entra TUDO, ou NADA.
-- ============================================================================
begin;

-- 1) Nos objetos que JÁ EXISTEM
revoke all     on all tables    in schema public from anon, authenticated;
revoke all     on all sequences in schema public from anon, authenticated;
revoke execute on all functions in schema public from anon, authenticated;

-- 2) Nos objetos FUTUROS. SEM ISTO O FIX NÃO SE SUSTENTA: hoje o default
--    privilege do `postgres` dá `anon=arwdDxtm` a toda tabela nova — a
--    próxima migração que criar uma tabela devolveria o acesso total sozinha.
--    (As tabelas do app são criadas pelo `postgres`, via DATABASE_URL: é esse
--    o default que importa.)
alter default privileges for role postgres in schema public
  revoke all     on tables    from anon, authenticated;
alter default privileges for role postgres in schema public
  revoke all     on sequences from anon, authenticated;
alter default privileges for role postgres in schema public
  revoke execute on functions from anon, authenticated;

commit;


-- ============================================================================
-- PASSO 3 — CONFERIR O DEPOIS (só leitura).
-- Esperado: anon = 0 e authenticated = 0 em tudo.
--           service_role continua 124 (é o do Storage — tem que continuar).
-- ============================================================================
select r.rolname                                                             as papel,
       count(*) filter (where has_table_privilege(r.rolname, c.oid, 'SELECT')) as select_,
       count(*) filter (where has_table_privilege(r.rolname, c.oid, 'INSERT')) as insert_,
       count(*) filter (where has_table_privilege(r.rolname, c.oid, 'UPDATE')) as update_,
       count(*) filter (where has_table_privilege(r.rolname, c.oid, 'DELETE')) as delete_,
       count(*)                                                               as tabelas
  from pg_roles r
 cross join pg_class c
  join pg_namespace n on n.oid = c.relnamespace and n.nspname = 'public'
 where r.rolname in ('anon', 'authenticated', 'service_role')
   and c.relkind = 'r'
 group by r.rolname
 order by r.rolname;

-- E o default privilege — tabela NOVA não pode mais nascer aberta.
-- Esperado: nas linhas do `postgres`, anon e authenticated SUMIRAM.
select pg_get_userbyid(d.defaclrole) as dono,
       d.defaclobjtype::text         as tipo,
       array_to_string(d.defaclacl, ' ') as quem_ganha_permissao
  from pg_default_acl d
  join pg_namespace n on n.oid = d.defaclnamespace and n.nspname = 'public'
 order by 1, 2;


-- ============================================================================
-- ROLLBACK — volta EXATAMENTE ao estado de hoje. Só use se algo quebrar.
-- (Não é pra rodar junto. Fica aqui pronto, caso precise.)
-- ============================================================================
-- begin;
-- grant all     on all tables    in schema public to anon, authenticated;
-- grant all     on all sequences in schema public to anon, authenticated;
-- grant execute on all functions in schema public to anon, authenticated;
-- alter default privileges for role postgres in schema public
--   grant all     on tables    to anon, authenticated;
-- alter default privileges for role postgres in schema public
--   grant all     on sequences to anon, authenticated;
-- alter default privileges for role postgres in schema public
--   grant execute on functions to anon, authenticated;
-- commit;


-- ============================================================================
-- NOTAS
-- ============================================================================
-- * POR QUE ESTE REVOKE REMOVE TUDO: no Postgres, só quem CONCEDEU uma
--   permissão consegue revogá-la. Conferido em 19/09 via aclexplode(relacl):
--   TODAS as concessões a anon e authenticated nas 124 tabelas foram feitas
--   pelo `postgres` — o mesmo papel que roda o SQL Editor. Então nada sobra.
--   (Se o Passo 3 mostrar algo diferente de 0, é sinal de que isso mudou —
--   pare e investigue antes de seguir.)
-- * A exposição era maior que "ler e escrever": as concessões incluem também
--   TRUNCATE, TRIGGER, REFERENCES e MAINTAIN. O TRUNCATE já esbarraria na
--   trava_truncate do banco, mas a permissão não deveria existir.
-- * O default privilege do `supabase_admin` também dá acesso ao anon, mas só
--   vale pra objeto criado PELO supabase_admin (internos do Supabase) — não é
--   o caminho das tabelas deste app. E o `postgres` provavelmente nem tem
--   permissão pra alterá-lo. Fica fora de propósito.
-- * Depois de aplicar, tabela nova criada por migração nasce SEM acesso público
--   E com RLS ligada (o event trigger rls_auto_enable continua fazendo isso).
--   Duas camadas desde o nascimento.
-- * Se um dia o app PRECISAR da API pública (ex.: um front lendo direto do
--   Supabase), a porta certa é dar GRANT só na tabela necessária + escrever a
--   policy dela — não reabrir tudo.
