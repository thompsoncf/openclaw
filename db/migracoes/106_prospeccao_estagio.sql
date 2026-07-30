-- 106_prospeccao_estagio.sql
-- Funil de 2 estágios: dado captado ('base') vs lead engajado ('lead').
-- A captação nova entra como 'base' (matéria-prima da campanha) e SÓ vira 'lead'
-- quando a pessoa engaja: topou no WhatsApp, respondeu WhatsApp/e-mail, clicou
-- "Tenho interesse" — ou promoção manual. O funil (kanban) mostra só 'lead'.
-- Idempotente.

alter table public.prospeccao add column if not exists estagio text not null default 'base';

-- Tudo que já existe hoje vira 'lead' — o funil atual não muda ao migrar.
-- (roda uma vez; migrações são rastreadas em schema_migrations)
update public.prospeccao set estagio='lead' where estagio is distinct from 'lead';

create index if not exists idx_prospeccao_estagio on public.prospeccao (conta_id, estagio);
