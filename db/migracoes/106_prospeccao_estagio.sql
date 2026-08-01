-- 106_prospeccao_estagio.sql
-- Funil de 2 estágios: dado captado ('base') vs lead engajado ('lead').
-- A captação nova entra como 'base' (matéria-prima da campanha) e SÓ vira 'lead'
-- quando a pessoa engaja: topou no WhatsApp, respondeu WhatsApp/e-mail, clicou
-- "Tenho interesse" — ou promoção manual. O funil (kanban) mostra só 'lead'.
-- Idempotente.

alter table public.prospeccao add column if not exists estagio text not null default 'base';

-- Backfill único e SEGURO: só quem JÁ engajou (respondeu no inbox/WhatsApp/e-mail
-- ou clicou — deixando uma mensagem de entrada, ou marcado 'respondeu' na campanha)
-- vira 'lead'. Todo o resto (base captada, ainda não engajada) fica 'base'.
--   ⚠️ ANTES esta linha promovia TODA a base pra 'lead' de uma vez
--   (update ... where estagio is distinct from 'lead'), o que jogava contatos
--   captados mas nunca contatados pro funil e bagunçava a operação. Corrigido.
update public.prospeccao p set estagio='lead'
 where p.estagio is distinct from 'lead'
   and (exists (select 1 from public.conversas cv
                  join public.mensagens m on m.conversa_id=cv.id
                 where cv.prospeccao_id=p.id and m.direcao='in')
        or exists (select 1 from public.campanha_alvos a
                    where a.prospeccao_id=p.id and a.status='respondeu'));

create index if not exists idx_prospeccao_estagio on public.prospeccao (conta_id, estagio);
