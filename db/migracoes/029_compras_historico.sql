-- 029_compras_historico.sql
-- Tabela pra guardar compras finalizadas (data + nº de itens + NOMES em jsonb).
-- Os itens comprados sao deletados da lista ativa.
-- Os PENDENTES continuam na lista pra proxima compra.
-- itens = array dos nomes comprados (pra mostrar no histórico).

create table if not exists compras_historico (
    id          bigserial primary key,
    conta_id    bigint not null references contas(id) on delete restrict,
    membro_id   bigint references membros(id) on delete set null,
    total_itens int not null default 0,
    itens       jsonb not null default '[]',
    criado_em   timestamptz not null default now()
);

create index if not exists idx_compras_hist_conta on compras_historico(conta_id);
create index if not exists idx_compras_hist_criado_em on compras_historico(criado_em);
