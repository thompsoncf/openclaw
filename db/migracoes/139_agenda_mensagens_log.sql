-- 139_agenda_mensagens_log.sql
-- Histórico de TENTATIVAS de envio de mensagem da agenda (convite, lembrete pro
-- convidado, lembrete pro dono, remarcado) — sucesso ou falha, com o motivo.
-- Hoje só existe lembretes_enviados (dedup: "já mandei"), que não guarda o
-- resultado nem o motivo de uma falha — foi por isso que o caso do aviso do
-- Paulo só deu pra investigar direto no banco. Essa tabela é só um LOG (nunca
-- decide comportamento de envio, isso continua em lembretes_enviados/convites);
-- alimenta a seção "Histórico de envios" no painel. Idempotente.

create table if not exists public.agenda_mensagens_log (
  id             bigserial primary key,
  conta_id       bigint not null references public.contas(id) on delete cascade,
  evento_id      bigint references public.eventos_agenda(id) on delete cascade,
  convidado_id   bigint references public.evento_convidados(id) on delete cascade,
  tipo           text not null check (tipo in ('convite','lembrete','remarcado')),
  canal          text not null,                          -- 'whatsapp_livre'|'whatsapp_template'|'telegram'|'manual'
  ok             boolean not null,
  motivo         text,                                   -- preenchido só quando ok=false
  criado_em      timestamptz not null default now()
);
create index if not exists idx_agenda_msg_log_conta_quando
  on public.agenda_mensagens_log (conta_id, criado_em desc);
