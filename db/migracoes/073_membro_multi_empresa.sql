-- 073_membro_multi_empresa.sql
-- Um login, várias empresas: a mesma pessoa (mesmo e-mail) pode ser membro de
-- mais de uma empresa. O e-mail deixa de ser único GLOBAL e passa a ser único
-- POR conta. Também garante contas.senha_hash (autoridade da identidade).
-- Espelhado em runtime por contas.equipe.garantir_tabela.
alter table contas add column if not exists senha_hash text;

drop index if exists idx_membros_email;
create unique index if not exists idx_membros_email_conta
    on membros (conta_id, lower(email)) where email is not null;
