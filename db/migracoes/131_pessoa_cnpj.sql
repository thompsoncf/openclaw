-- 131_pessoa_cnpj.sql
-- Cliente pode ser PESSOA JURIDICA (PJ). A identidade (pessoas) ate agora so'
-- tinha CPF; aqui ganha CNPJ (unico) e um TIPO (pf/pj) pra exibir e filtrar.
-- Nao-destrutivo e idempotente: o cadastro por CPF continua igual.

alter table pessoas add column if not exists cnpj text;
alter table pessoas add column if not exists tipo text;

-- CNPJ e' chave forte (funde na igualdade), igual o CPF.
create unique index if not exists ux_pessoas_cnpj on pessoas (cnpj) where cnpj is not null;
create index if not exists ix_pessoas_tipo on pessoas (tipo) where tipo is not null;

-- backfill: quem ja tem CNPJ vira PJ; o resto e' PF.
update pessoas set tipo = case when cnpj is not null then 'pj' else 'pf' end
 where tipo is null;

alter table pessoas alter column tipo set default 'pf';

-- rollback (manual):
--   alter table pessoas drop column if exists cnpj;
--   alter table pessoas drop column if exists tipo;
