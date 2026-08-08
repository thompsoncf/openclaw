-- 136_visita_agenda.sql
-- Agendar visita ao espaço pelo Cockpit: a visita é um evento da agenda que já existe
-- (eventos_agenda). Aqui só ligamos o evento ao LEAD e damos um token pro convite .ics
-- público (o cliente abre o link e adiciona no calendário dele, com lembrete).
-- Aditivo e idempotente.

alter table public.eventos_agenda add column if not exists prospeccao_id bigint
  references public.prospeccao(id) on delete set null;
alter table public.eventos_agenda add column if not exists ics_token text;

create unique index if not exists idx_eventos_ics_token
  on public.eventos_agenda (ics_token) where ics_token is not null;
create index if not exists idx_eventos_prospeccao
  on public.eventos_agenda (prospeccao_id) where prospeccao_id is not null;
