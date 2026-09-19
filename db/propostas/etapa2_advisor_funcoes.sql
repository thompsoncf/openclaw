-- ============================================================================
--  APLICADO em 19/09/2026, à mão, no SQL Editor. Conferido depois pelo
--  conector somente-leitura: rls_auto_enable só com postgres e service_role
--  (anon/authenticated = false); trava_* com search_path=pg_catalog, public;
--  event trigger ensure_rls ligado (O). Advisor de segurança: ZERO alertas WARN
--  — sobra só o INFO "RLS Enabled No Policy", esperado até o Passo B.
-- ============================================================================
--  PROPOSTA — NÃO É MIGRAÇÃO. NÃO RODA SOZINHA.
--
--  Mora em db/propostas/, fora de db/migracoes/: o deploy não lê esta pasta.
--  Quem executa é uma pessoa, à mão, no SQL Editor do Supabase.
--
--  O QUE FAZ: fecha os dois alertas de segurança que o advisor do Supabase
--  ainda mostra depois do revoke_papeis_publicos.sql:
--    1. rls_auto_enable() pode ser executada pelo público (anon/authenticated)
--       via /rest/v1/rpc — ela é SECURITY DEFINER.
--    2. as três trava_* não têm search_path fixo.
--
--  O QUE NÃO FAZ:
--    - não altera NENHUMA linha de dado, não apaga NADA
--    - não toca em NENHUMA chave
--    - não muda o que as funções fazem: o corpo delas fica igual
--    - não desliga nenhum gatilho: as travas e o rls_auto_enable seguem ativos
--  Muda só PERMISSÃO e CONFIGURAÇÃO de 4 funções. Reversível (bloco ROLLBACK).
--
-- ----------------------------------------------------------------------------
--  POR QUE É SEGURO (medido em 19/09/2026, conector somente-leitura)
-- ----------------------------------------------------------------------------
--  * rls_auto_enable: é a função do event trigger `ensure_rls` (ddl_command_end,
--    ligado), que liga a RLS em toda tabela nova. O Postgres NÃO confere
--    EXECUTE quando dispara gatilho — só quem chama a função à mão precisa.
--    Tirar o EXECUTE do PUBLIC fecha a chamada pela API e não afeta o gatilho.
--    O dono (postgres) e o service_role mantêm o EXECUTE explícito.
--    Ela não existe em db/migracoes/ (foi criada no painel do Supabase), então
--    nenhuma migração a recria por cima.
--  * trava_*: o corpo delas só usa current_setting() (pg_catalog, sempre
--    visível) e a tabela de transição `deletadas` (do próprio gatilho, achada
--    antes de qualquer schema). Fixar search_path = pg_catalog, public dá
--    EXATAMENTE a resolução de nomes de hoje (o padrão é "$user", public), só
--    que sem depender de quem chama. Criadas em 026/027, que já rodaram e não
--    rodam de novo — nada sobrescreve a configuração.
-- ============================================================================


-- ============================================================================
-- PASSO 0 — FOTO DO ANTES (só leitura).
-- Esperado hoje: as 4 com "=X/postgres" (o "=" sem nome é o PUBLIC);
--                trava_* com config vazia; rls_auto_enable com search_path=pg_catalog.
-- ============================================================================
select p.proname                          as funcao,
       array_to_string(p.proacl, ' ')     as quem_executa,
       p.proconfig                        as config
  from pg_proc p
  join pg_namespace n on n.oid = p.pronamespace and n.nspname = 'public'
 where p.proname in ('rls_auto_enable', 'trava_apagar_conta',
                     'trava_lancamento_massa', 'trava_truncate')
 order by 1;


-- ============================================================================
-- PASSO 1 — A MUDANÇA. Numa transação: ou entra TUDO, ou NADA.
-- ============================================================================
begin;

-- 1) rls_auto_enable: o público deixa de poder chamar pela API
revoke execute on function public.rls_auto_enable() from public, anon, authenticated;

-- 2) trava_*: search_path fixo, igual à resolução de hoje
alter function public.trava_apagar_conta()     set search_path = pg_catalog, public;
alter function public.trava_lancamento_massa() set search_path = pg_catalog, public;
alter function public.trava_truncate()         set search_path = pg_catalog, public;

commit;


-- ============================================================================
-- PASSO 2 — CONFERIR O DEPOIS (só leitura).
-- Esperado: rls_auto_enable SEM o "=X/postgres" (fica postgres e service_role);
--           trava_* com {search_path=pg_catalog, public};
--           event trigger ensure_rls continua com enabled = O (ligado).
-- ============================================================================
select p.proname                          as funcao,
       array_to_string(p.proacl, ' ')     as quem_executa,
       p.proconfig                        as config,
       has_function_privilege('anon', p.oid, 'EXECUTE') as anon_executa
  from pg_proc p
  join pg_namespace n on n.oid = p.pronamespace and n.nspname = 'public'
 where p.proname in ('rls_auto_enable', 'trava_apagar_conta',
                     'trava_lancamento_massa', 'trava_truncate')
 order by 1;

select evtname, evtevent, evtenabled::text from pg_event_trigger where evtname = 'ensure_rls';


-- ============================================================================
-- ROLLBACK — volta EXATAMENTE ao estado de hoje. Só use se algo quebrar.
-- ============================================================================
-- begin;
-- grant execute on function public.rls_auto_enable() to public;
-- alter function public.trava_apagar_conta()     reset search_path;
-- alter function public.trava_lancamento_massa() reset search_path;
-- alter function public.trava_truncate()         reset search_path;
-- commit;


-- ============================================================================
-- NOTAS
-- ============================================================================
-- * As trava_* continuam com EXECUTE pro PUBLIC de propósito: são funções de
--   gatilho comum (retornam `trigger`), o Postgres recusa chamá-las direto e a
--   API não as expõe. O advisor não as aponta por isso.
-- * O alerta "RLS Enabled No Policy" (INFO, 124 tabelas) CONTINUA depois disto
--   e está certo que continue: é o estado desejado até o Passo B (policies).
--   RLS ligada sem policy = ninguém de fora lê nada.
