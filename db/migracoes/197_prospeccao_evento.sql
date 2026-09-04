-- 197_prospeccao_evento.sql
-- O EVENTO no lead: tipo, data e convidados.
--
-- POR QUE. Medido na Prime Eventos (conta 34) em 04/09/2026: 274 leads no funil,
-- 224 numa coluna só, e a data da festa conhecida em só 15 — porque o agente
-- pergunta a data em toda conversa de preço mas guardava a resposta apenas em
-- `orcamentos.evento`, que o funil nunca lê. No nicho de eventos o mês que
-- importa é o da festa, não o mês em que o cliente escreveu: é por essa data que
-- o funil passa a agrupar e filtrar (finance/evento_lead.py).
--
-- Aditivo e idempotente. O backfill copia do orçamento já amarrado ao lead
-- (`prospeccao.orcamento_id`), só onde a data é reconhecível (ISO ou dd/mm/aaaa)
-- e só pra lead que ainda não tem — nada é sobrescrito.
alter table public.prospeccao add column if not exists evento_em         date;
alter table public.prospeccao add column if not exists evento_tipo       text;
alter table public.prospeccao add column if not exists evento_convidados integer;

create index if not exists idx_prospeccao_evento_em
  on public.prospeccao (conta_id, evento_em) where evento_em is not null;

update public.prospeccao p
   set evento_em = coalesce(p.evento_em,
         case when o.evento->>'data' ~ '^\d{4}-\d{2}-\d{2}'
                then to_date(substr(o.evento->>'data', 1, 10), 'YYYY-MM-DD')
              when o.evento->>'data' ~ '^\d{1,2}/\d{1,2}/\d{4}'
                then to_date(o.evento->>'data', 'DD/MM/YYYY')
              else null end),
       evento_tipo = coalesce(p.evento_tipo, nullif(btrim(o.evento->>'tipo'), '')),
       evento_convidados = coalesce(p.evento_convidados,
         case when o.evento->>'convidados' ~ '^\d+$'
                then nullif((o.evento->>'convidados')::int, 0) else null end)
  from public.orcamentos o
 where o.id = p.orcamento_id and o.evento is not null
   and (p.evento_em is null or p.evento_tipo is null or p.evento_convidados is null);
