-- 452_evento_stands_sinal_saldo_grupo.sql
-- Estandes: até 2 por empresa num contrato só, sinal mínimo por stand e saldo
-- com data (aprovado na maquete em 30/09/2026).
--
-- * evento_stands.grupo_id: os 2 estandes reservados juntos (mesma proposta,
--   mesmo contrato, mesmo comprovante). NULL = estande sozinho.
-- * evento_stands_config: o sinal mínimo POR ESTANDE (padrão R$ 1.500), o teto de
--   estandes por empresa (2), a data-limite do saldo (NULL = o dia do evento) e o
--   horário de funcionamento, que o contrato mostra no cabeçalho.
--
-- Aditiva e idempotente.
alter table public.evento_stands add column if not exists grupo_id text;
create index if not exists idx_evento_stands_grupo
    on public.evento_stands (conta_id, grupo_id) where grupo_id is not null;

alter table public.evento_stands_config
    add column if not exists sinal_minimo_centavos int not null default 150000;
alter table public.evento_stands_config
    add column if not exists max_por_empresa int not null default 2;
alter table public.evento_stands_config add column if not exists saldo_ate date;
alter table public.evento_stands_config add column if not exists evento_horario text;

-- rollback:
--   alter table public.evento_stands_config drop column if exists evento_horario,
--     drop column if exists saldo_ate, drop column if exists max_por_empresa,
--     drop column if exists sinal_minimo_centavos;
--   drop index if exists idx_evento_stands_grupo;
--   alter table public.evento_stands drop column if exists grupo_id;
