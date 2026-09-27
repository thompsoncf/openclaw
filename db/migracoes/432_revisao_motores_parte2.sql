-- Revisão dos motores do funil, parte 2 (27/09/2026).
--
-- `visita_rotinas.remarcado_em`: quando a visita da equipe foi remarcada pela última
-- vez. A véspera e o "2h antes" do horário novo contam a partir daí — antes, remarcar
-- às 18h20 pra amanhã fazia a pergunta da véspera sair logo depois do "sua visita
-- mudou de data".
--
-- Não toca conversa, mensagem, conexão, chip nem `canais_config` (CLAUDE.md §0/§1),
-- nem card nenhum. Aditiva e idempotente.

alter table if exists public.visita_rotinas
  add column if not exists remarcado_em timestamptz;

-- `avisos_adiados`: o aviso à equipe que nasceu de madrugada espera a manhã
-- (finance/aviso_noite.py). A regra do negócio não espera — só o aviso.
create table if not exists public.avisos_adiados (
  id          bigserial primary key,
  conta_id    bigint not null,
  membro_id   bigint,
  destino     text not null default 'membro',
  titulo      text not null default '',
  corpo       text not null default '',
  url         text not null default '',
  criado_em   timestamptz not null default now(),
  enviado_em  timestamptz
);

create index if not exists avisos_adiados_pendentes_idx
  on public.avisos_adiados (id) where enviado_em is null;
