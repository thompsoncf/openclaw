create table if not exists orcamentos (
    id                     bigserial primary key,
    cliente                text,
    empresa                text,
    segmento               text,
    setup_centavos         bigint default 0,
    mensal_centavos        bigint default 0,
    primeiro_ano_centavos  bigint default 0,
    n_modulos              int default 0,
    criado_em              timestamptz default now()
);

create index if not exists idx_orcamentos_criado_em on orcamentos (criado_em desc);
