-- Migracao 048 -- confirmacao de e-mail
alter table contas add column if not exists email_confirmado_em timestamptz;
alter table contas add column if not exists email_pendente text;

create table if not exists tokens_email (
    token       text primary key,
    conta_id    bigint not null references contas(id),
    email_novo  text not null,
    criado_em   timestamptz not null default now(),
    expira_em   timestamptz not null,
    usado       boolean not null default false
);
create index if not exists ix_token_email_conta on tokens_email (conta_id);
-- ROLLBACK: drop table if exists tokens_email; alter table contas drop column if exists email_pendente; alter table contas drop column if exists email_confirmado_em;
