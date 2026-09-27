-- 398_resgate_toques.sql
-- ETAPA 2 DO RESGATE DA IA (docs/mockups/resgate_ia.html): os toques e o perdido.
-- A regra de sempre do dono — "3 toques e o 4º fica como perdido" — feita pela IA:
-- a retomada (toque 1), o 2º toque no dia 3, o 3º no dia 7, e sem resposta no dia 10
-- o lead vira perdido, motivo "não respondeu" (finance/resgate.py).
--
-- Só colunas novas em `resgate_leads`, tabela do próprio resgate (396). Não toca em
-- `canais_config` nem em nada da conexão (CLAUDE.md §1). Aditiva e idempotente.

alter table public.resgate_leads
  add column if not exists toques     smallint not null default 1,
  add column if not exists perdido_em timestamptz;

-- rollback:
--   alter table public.resgate_leads drop column if exists toques,
--     drop column if exists perdido_em;
