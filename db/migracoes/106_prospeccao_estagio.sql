-- 106_prospeccao_estagio.sql
-- Funil de 2 estágios: dado captado ('base') vs lead engajado ('lead').
-- A captação nova entra como 'base' (matéria-prima da campanha) e SÓ vira 'lead'
-- quando a pessoa engaja: topou no WhatsApp, respondeu WhatsApp/e-mail, clicou
-- "Tenho interesse" — ou promoção manual. O funil (kanban) mostra só 'lead'.
-- Idempotente.

alter table public.prospeccao add column if not exists estagio text not null default 'base';

-- Backfill do momento da migração: o que já existia vira 'lead' (não muda o funil de
-- quem já usava). Em instalação NOVA a tabela está vazia → afeta 0 linhas (inofensivo).
--   Nota: numa conta que já tinha BASE captada (não engajada) quando esta migração
--   rodou pela 1ª vez, isso promoveu contatos indevidamente pro funil. Correção é
--   pontual, por dado (feita direto no banco) — não dá pra condicionar aqui sem
--   depender de tabelas que podem não existir neste ponto da sequência.
update public.prospeccao set estagio='lead' where estagio is distinct from 'lead';

create index if not exists idx_prospeccao_estagio on public.prospeccao (conta_id, estagio);
