create table if not exists repasses (
    id serial primary key,
    fornecedor_id integer not null references contas(id),
    valor_centavos integer not null,
    data_repasse date not null default current_date,
    observacao text,
    criado_em timestamptz not null default now(),
    criado_por integer references contas(id)
);
create index if not exists idx_repasses_forn on repasses(fornecedor_id, data_repasse desc);
