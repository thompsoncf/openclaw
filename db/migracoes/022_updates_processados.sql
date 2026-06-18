create table if not exists updates_processados (
    update_id  bigint primary key,
    criado_em  timestamptz not null default now()
);

-- Limpeza periodica: comentada, pode ser rodada manualmente
-- delete from updates_processados where criado_em < now() - interval '7 days';
