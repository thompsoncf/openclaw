create table if not exists uso_api (
    id                  bigserial primary key,
    conta_id            bigint,
    input_tokens        int not null default 0,
    cache_read_tokens   int not null default 0,
    cache_write_tokens  int not null default 0,
    output_tokens       int not null default 0,
    custo_centavos      int not null default 0,
    eh_foto             boolean not null default false,
    criado_em           timestamptz not null default now()
);
create index if not exists ix_uso_api_data on uso_api (criado_em);
create index if not exists ix_uso_api_conta on uso_api (conta_id);
