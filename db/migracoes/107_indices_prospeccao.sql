-- 107_indices_prospeccao.sql
-- Desempenho: índices pras queries quentes da prospecção que ficaram sem cobertura.
-- Idempotente.

-- campanha_alvos por prospeccao_id: a aba Base (lateral join por lead), a checagem
-- de "já está na campanha" e o "viraram lead" filtram/juntam por prospeccao_id — o
-- índice único (campanha_id, prospeccao_id) não serve porque a coluna líder é campanha_id.
create index if not exists idx_alvos_prospeccao on public.campanha_alvos (prospeccao_id);

-- contadores por campanha na lista (enviados/respostas) filtram (campanha_id, status).
create index if not exists idx_alvos_camp_status on public.campanha_alvos (campanha_id, status);
