-- 098_agenda.sql
-- Agenda PRÓPRIA do Zaq (sem depender de Google/OAuth). Cada evento pertence a
-- uma CONTA (agenda da família/empresa); membro_id guarda quem marcou. Datas em
-- timestamptz (aware). O horário do usuário é interpretado em horário de Brasília.
-- Idempotente.

create table if not exists public.eventos_agenda (
  id           bigserial primary key,
  conta_id     bigint not null references public.contas(id) on delete cascade,
  membro_id    bigint references public.membros(id) on delete set null,
  titulo       text not null,
  inicio       timestamptz not null,
  fim          timestamptz,
  local        text,
  descricao    text,
  lembrete_min int,                                    -- min antes p/ avisar (null = sem aviso individual)
  status       text not null default 'ativo' check (status in ('ativo','cancelado')),
  criado_em    timestamptz not null default now()
);
create index if not exists idx_eventos_conta_inicio
  on public.eventos_agenda (conta_id, inicio) where status = 'ativo';

-- Config do LEMBRETE por conta (opt-in: desligado por padrão). O botão do portal
-- e a ferramenta do chat mexem aqui. feed_token alimenta o .ics assinável (etapa 3).
create table if not exists public.agenda_config (
  conta_id        bigint primary key references public.contas(id) on delete cascade,
  lembrete_ativo  boolean not null default false,
  hora_resumo     int not null default 7,              -- hora (0-23, Brasília) do "resumo do dia"
  aviso_antes_min int,                                  -- null = sem aviso antes de cada evento
  feed_token      text unique,                          -- .ics assinável (etapa 3)
  atualizado_em   timestamptz not null default now()
);
