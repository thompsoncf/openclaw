-- 351_clinica_agenda.sql
-- Fase 2 da clínica: a agenda do dia (docs/mockups/clinica_visao_geral.html,
-- seção 10; telas Agenda e Novo agendamento do clinica_prototipo.html).
--
-- O AGENDAMENTO É UMA LINHA DE `eventos_agenda`, a mesma agenda da conta. Assim o
-- funil (gatilho "compromisso", que já lê essa tabela com prospeccao_id), o
-- lembrete e qualquer tela de agenda enxergam a consulta sem código novo. Entra
-- como tipo 'empresa', sem `tipo_evento` e sem `ocupa_espaco`: nada da lógica de
-- festa (pré-reserva, espaço ocupado) se aplica.
--
-- OS SETE STATUS (`situacao`), aprovados no protótipo:
--   agendado → confirmado → presente → atendimento → finalizado, ou faltou / cancelou.
-- `status` ('ativo'/'cancelado') continua sendo o da agenda de sempre: cancelou
-- também grava status='cancelado', pra toda leitura antiga tirar da conta.
--
-- PACIENTE: o card do funil (`prospeccao_id`). O nome e o celular ficam copiados no
-- agendamento porque é o que a recepção lê na coluna e o que a confirmação usa.
-- Nada de queixa, diagnóstico ou exame.
--
-- A CONFIRMAÇÃO NA VÉSPERA nasce desligada (clinica_agenda_config.confirmacao_modo).
--
-- Aditiva e idempotente.

alter table public.eventos_agenda
  add column if not exists profissional_id bigint references public.clinica_profissionais(id) on delete set null,
  add column if not exists servico_id bigint references public.servicos_catalogo(id) on delete set null,
  add column if not exists clinica_local_id bigint references public.clinica_locais(id) on delete set null,
  add column if not exists situacao text,
  add column if not exists situacao_em timestamptz,
  add column if not exists origem text,
  add column if not exists paciente_nome text,
  add column if not exists paciente_fone text,
  add column if not exists encaixe boolean not null default false,
  add column if not exists confirmacao_enviada_em timestamptz,
  add column if not exists confirmado_em timestamptz,
  add column if not exists pede_remarcar_em timestamptz;

do $$ begin
  if not exists (select 1 from pg_constraint where conname = 'eventos_agenda_situacao_check') then
    alter table public.eventos_agenda add constraint eventos_agenda_situacao_check
      check (situacao is null or situacao in
             ('agendado','confirmado','presente','atendimento','finalizado','faltou','cancelou'));
  end if;
end $$;

create index if not exists idx_eventos_clinica_prof
  on public.eventos_agenda (conta_id, profissional_id, inicio) where situacao is not null;

create table if not exists public.clinica_agenda_config (
  conta_id bigint primary key references public.contas(id),
  confirmacao_modo text not null default 'off' check (confirmacao_modo in ('off','ligado')),
  confirmacao_hora smallint not null default 10 check (confirmacao_hora between 7 and 19),
  atualizado_em timestamptz not null default now());

-- rollback:
--   drop table if exists public.clinica_agenda_config;
--   drop index if exists public.idx_eventos_clinica_prof;
--   alter table public.eventos_agenda drop constraint if exists eventos_agenda_situacao_check;
--   alter table public.eventos_agenda drop column if exists profissional_id, drop column if exists servico_id,
--     drop column if exists clinica_local_id, drop column if exists situacao, drop column if exists situacao_em,
--     drop column if exists origem, drop column if exists paciente_nome, drop column if exists paciente_fone,
--     drop column if exists encaixe, drop column if exists confirmacao_enviada_em,
--     drop column if exists confirmado_em, drop column if exists pede_remarcar_em;
