-- 059_contato_empresa.sql
-- Contato da empresa (email e telefone), separados do email de LOGIN da conta.
-- O email da Receita pode diferir do email de acesso, por isso coluna própria.
-- Idempotente.

alter table contas add column if not exists email_empresa text;
alter table contas add column if not exists telefone     text;

-- rollback:
--   alter table contas drop column if exists email_empresa,
--     drop column if exists telefone;
