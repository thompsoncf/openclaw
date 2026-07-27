-- 101_agenda_lembretes.sql
-- Suporte ao lembrete proativo da agenda (etapa 2):
--  1) resumo_ativo: separa "resumo do dia ligado" de "aviso antes ligado" — antes
--     não dava pra distinguir (o cron mandaria resumo mesmo pra quem só queria aviso).
--  2) lembretes_enviados: idempotência — cada resumo (por dia) e cada aviso (por
--     evento) sai UMA vez, mesmo com o ticker rodando de 2 em 2 min e 2 workers.
-- Idempotente.

alter table public.agenda_config
  add column if not exists resumo_ativo boolean not null default false;

-- Backfill (roda 1x): no schema antigo o "resumo do dia" aparecia ligado sempre
-- que o lembrete estava ligado — preserva isso pra ninguém perder o que já tinha.
update public.agenda_config set resumo_ativo = true
  where lembrete_ativo = true and resumo_ativo = false;

create table if not exists public.lembretes_enviados (
  id          bigserial primary key,
  conta_id    bigint not null references public.contas(id) on delete cascade,
  tipo        text not null check (tipo in ('resumo','aviso')),
  chave       text not null,                 -- resumo: 'AAAA-MM-DD'; aviso: 'evt:<id>'
  enviado_em  timestamptz not null default now(),
  unique (conta_id, tipo, chave)
);
