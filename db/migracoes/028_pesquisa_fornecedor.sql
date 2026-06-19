-- 028_pesquisa_fornecedor.sql
-- Tabela pra guardar respostas de pesquisa de fornecedores (Zaq Fornecedor fase 1).
-- Reutilizavel por segmento (hortifruti, padaria, acougue, etc).
-- jsonb = sem precisar migrar quando as perguntas mudam.

create table if not exists pesquisa_fornecedor (
    id          bigserial primary key,
    segmento    text not null default 'hortifruti',
    respostas   jsonb not null,
    criado_em   timestamptz not null default now()
);

create index if not exists ix_pesquisa_segmento on pesquisa_fornecedor(segmento);
create index if not exists ix_pesquisa_criado_em on pesquisa_fornecedor(criado_em);
