-- 060_unidade_generica.sql
-- Remove o CHECK rígido de unidade em catalogo_produtos.
-- Antes: só aceitava kg/duzia/unidade/maco/bandeja/litro/pacote (hortifrúti).
-- Agora: o módulo Vendas é genérico por nicho — cada nicho tem suas unidades
-- (peça, caixa, ml, comprimido...). A validação passou pro código (finance/nichos.py).
-- O banco só guarda a unidade como texto livre; quem valida é a aplicação.
-- Idempotente (drop constraint if exists).

-- O nome do constraint é gerado pelo Postgres a partir da tabela+coluna.
-- Em geral: catalogo_produtos_unidade_check. Removemos com segurança.
do $$
begin
  if exists (
    select 1 from information_schema.constraint_column_usage
    where table_name = 'catalogo_produtos' and column_name = 'unidade'
      and constraint_name = 'catalogo_produtos_unidade_check'
  ) then
    alter table catalogo_produtos drop constraint catalogo_produtos_unidade_check;
  end if;
end $$;

-- rollback (repõe o check antigo — só se precisar voltar):
--   alter table catalogo_produtos add constraint catalogo_produtos_unidade_check
--     check (unidade in ('kg','duzia','unidade','maco','bandeja','litro','pacote'));
