-- ============================================================
-- CHECAGEM da migração 031 — rodar no Render Shell DEPOIS de aplicar
--   psql "$DATABASE_URL" -f db/checar_migracao_031.sql
-- Cada bloco diz OK ou FALTA. Se algo disser FALTA, a migração não
-- aplicou direito — parar e reportar.
-- ============================================================

\echo '==== 1. Tabela NICHOS existe e tem hortifruti? ===='
select
  case when count(*) >= 1 then 'OK - nichos existe, ' || count(*) || ' nicho(s)'
       else 'FALTA - nenhum nicho' end as resultado
from nichos;
select id, nome, slug, tipo, ativo from nichos;

\echo ''
\echo '==== 2. Colunas de FORNECEDOR na tabela contas? ===='
select
  string_agg(column_name, ', ') as colunas_encontradas,
  case when count(*) = 5 then 'OK - 5 colunas'
       else 'FALTA - esperado 5, achou ' || count(*) end as resultado
from information_schema.columns
where table_name = 'contas'
  and column_name in ('eh_fornecedor','nicho_id','comissao_pct','asaas_wallet_id','fornecedor_slug');

\echo ''
\echo '==== 3. eh_fornecedor tem default FALSE (zero impacto)? ===='
select column_name, column_default,
  case when column_default like '%false%' then 'OK - default false'
       else 'ATENCAO - conferir default' end as resultado
from information_schema.columns
where table_name = 'contas' and column_name = 'eh_fornecedor';

\echo ''
\echo '==== 4. Tabela FORNECEDOR_FISCAL existe com os campos? ===='
select
  case when count(*) >= 10 then 'OK - fornecedor_fiscal com ' || count(*) || ' colunas'
       else 'FALTA - esperado >=10, achou ' || count(*) end as resultado
from information_schema.columns
where table_name = 'fornecedor_fiscal';

\echo ''
\echo '==== 5. Tabela VINCULOS existe (modelo cliente-centrico)? ===='
select
  case when count(*) >= 8 then 'OK - vinculos com ' || count(*) || ' colunas'
       else 'FALTA - esperado >=8, achou ' || count(*) end as resultado
from information_schema.columns
where table_name = 'vinculos';

\echo ''
\echo '==== 6. Indices de vinculos criados? ===='
select indexname from pg_indexes
where tablename = 'vinculos' and indexname like 'ix_vinculos%';

\echo ''
\echo '==== 7. NENHUMA conta foi marcada como fornecedor por engano? ===='
select
  case when count(*) = 0 then 'OK - nenhuma conta eh_fornecedor (esperado: 0 no inicio)'
       else 'ATENCAO - ' || count(*) || ' conta(s) ja marcada(s)' end as resultado
from contas where eh_fornecedor = true;

\echo ''
\echo '==== 8. Contas existentes intactas (amostra)? ===='
select id, nome, tipo, status, eh_fornecedor
from contas order by id limit 5;

\echo ''
\echo '==== RESUMO: se os 1-7 deram OK, a migracao 031 foi bem-sucedida. ===='
