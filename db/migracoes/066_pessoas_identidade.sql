-- 066_pessoas_identidade.sql
-- Camada de IDENTIDADE do cliente. Ate agora 'clientes' era a base privada de cada
-- loja (dono_id), mas cada loja tinha seu proprio registro da mesma pessoa. Aqui
-- separamos IDENTIDADE (pessoas: cpf/celular, unica no sistema) da RELACAO loja<->pessoa
-- (clientes ganha pessoa_id). Uma pessoa que compra em varias lojas e' UMA so, puxavel
-- por CPF/celular sem duplicar — e portavel pra uma conta ZAQ no futuro. Tambem liga a
-- VENDA ao cliente (lancamentos.cliente_id).
-- Idempotente. NAO derruba colunas de clientes (nome/telefone ficam como cache; limpeza
-- futura pode remove-las depois que o refactor provar-se).

-- 1) IDENTIDADE (unica no sistema)
create table if not exists pessoas (
    id            bigserial primary key,
    cpf           text,
    celular       text,
    nome          text not null,
    email         text,
    conta_zaq_id  bigint references contas(id),
    criado_em     timestamptz not null default now(),
    atualizado_em timestamptz not null default now()
);
create unique index if not exists ux_pessoas_cpf on pessoas (cpf) where cpf is not null;
create index if not exists ix_pessoas_celular on pessoas (celular) where celular is not null;

-- 2) RELACAO loja<->pessoa ganha a identidade
alter table clientes add column if not exists pessoa_id bigint references pessoas(id);
create index if not exists ix_clientes_pessoa on clientes (pessoa_id);

-- 3) BACKFILL 1:1 — cada cliente atual vira uma pessoa (leva nome/telefone/email/conta_zaq).
--    NAO funde por telefone (celular so' sugere; familia divide numero).
do $$
declare
  r record;
  pid bigint;
begin
  for r in select id, nome, telefone, email, conta_zaq_id, criado_em
             from clientes where pessoa_id is null loop
    insert into pessoas (nome, celular, email, conta_zaq_id, criado_em)
      values (r.nome, r.telefone, r.email, r.conta_zaq_id, coalesce(r.criado_em, now()))
      returning id into pid;
    update clientes set pessoa_id = pid where id = r.id;
  end loop;
end $$;

-- 4) liga a VENDA ao cliente (venda de balcao preenche; nullable no resto)
alter table lancamentos add column if not exists cliente_id bigint references clientes(id);
create index if not exists ix_lancamentos_cliente on lancamentos (cliente_id) where cliente_id is not null;

-- rollback (manual, cuidado):
--   alter table lancamentos drop column if exists cliente_id;
--   alter table clientes drop column if exists pessoa_id;
--   drop table if exists pessoas;
