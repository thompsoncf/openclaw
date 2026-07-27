-- 100_evento_convidados.sql
-- Convidados de um evento da agenda (reunião com confirmação). Cada convidado tem
-- um token pra a página pública de confirmação (/convite/<token>) — o cliente
-- confirma/recusa/pede pra remarcar sem login. conta_id é redundante (vem do
-- evento) mas fica aqui pra escopo e pra avisar o dono sem join extra. Idempotente.

create table if not exists public.evento_convidados (
  id             bigserial primary key,
  evento_id      bigint not null references public.eventos_agenda(id) on delete cascade,
  conta_id       bigint not null references public.contas(id) on delete cascade,
  nome           text,
  contato        text,                                 -- telefone/whatsapp como digitado (livre)
  token          text not null unique,                 -- segredo da página de confirmação
  status         text not null default 'pendente'
                 check (status in ('pendente','confirmado','remarcar','recusado')),
  resposta       text,                                 -- sugestão de horário / motivo (texto do cliente)
  respondido_em  timestamptz,
  criado_em      timestamptz not null default now()
);
create index if not exists idx_convidados_conta_evento
  on public.evento_convidados (conta_id, evento_id);
