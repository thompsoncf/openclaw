-- 046_indices_performance.sql
-- Indices pro painel /admin/comunicacao e pro expurgo por data.
-- (a tabela conversas_log cresce 1 linha por turno de conversa e nao tinha indice)

create index if not exists idx_conversas_criado_em
    on conversas_log (criado_em desc);

create index if not exists idx_conversas_conta_data
    on conversas_log (conta_id, criado_em desc);
