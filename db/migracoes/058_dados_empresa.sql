-- 058_dados_empresa.sql
-- Campos da empresa pro cadastro pós-upgrade PF->PJ.
-- contas já tem: documento (CNPJ), razao_social, endereco, cep, bairro.
-- Faltam: nome_fantasia, cidade, uf. Idempotente.

alter table contas add column if not exists nome_fantasia text;
alter table contas add column if not exists cidade        text;
alter table contas add column if not exists uf            varchar(2);

-- rollback:
--   alter table contas drop column if exists nome_fantasia,
--     drop column if exists cidade, drop column if exists uf;
