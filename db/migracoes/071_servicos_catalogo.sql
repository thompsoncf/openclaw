-- 071_servicos_catalogo.sql
-- Catálogo de serviços POR CONTA (multi-tenant). Cada empresa monta o próprio
-- catálogo do que vende, editável na aba Serviços do painel. Substitui a lista
-- de módulos que era chumbada no código. Empresa nova começa vazia.
-- add-if-not-exists: idempotente; o mesmo é garantido em runtime por
-- finance.servicos_catalogo.garantir_tabela (o deploy não roda migração sozinho).
create table if not exists servicos_catalogo (
    id              bigserial primary key,
    conta_id        bigint not null references contas(id) on delete cascade,
    slug            text not null,
    nome            text not null,
    descricao       text default '',
    setup_centavos  bigint default 0,
    mensal_centavos bigint default 0,
    custo_centavos  bigint default 0,
    ativo           boolean not null default true,
    ordem           int default 0,
    criado_em       timestamptz not null default now(),
    unique (conta_id, slug)
);
create index if not exists idx_servicos_catalogo_conta
    on servicos_catalogo (conta_id, ativo, ordem);
