-- Migração 041 — tokens de recuperação de senha
create table if not exists tokens_reset_senha (
    token       text primary key,
    conta_id    bigint not null references contas(id),
    criado_em   timestamptz not null default now(),
    expira_em   timestamptz not null,
    usado       boolean not null default false
);
create index if not exists ix_token_reset_conta on tokens_reset_senha (conta_id);
-- ROLLBACK: drop table if exists tokens_reset_senha;
