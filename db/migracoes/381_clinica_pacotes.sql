-- 381_clinica_pacotes.sql
-- Fase 6 da clínica: PACOTE COM SALDO, RETORNO PROGRAMADO e o lembrete que traz o
-- paciente de volta (docs/mockups/clinica_planos_pacotes_assinatura.html, seção 04;
-- clinica_visao_geral.html, "dois meses depois — retorno"). finance/clinica_pacotes.py.
--
--   * O plano aceito (379) vira SALDO: um pacote por procedimento do catálogo, com
--     as sessões compradas, o intervalo entre elas e a validade (12 meses, decisão B).
--   * Cada atendimento finalizado daquele procedimento BAIXA uma sessão, com data e
--     o agendamento (o histórico é o que responde "quanto foi usado" num reembolso).
--   * O RETORNO que o médico pede ao finalizar vira um prazo; 7 dias antes, o
--     paciente é chamado. Marcou com o profissional, o retorno sai da fila.
--   * Os LEMBRETES automáticos (próxima sessão liberada, retorno chegando, pacote
--     perto de vencer) ficam registrados: é o que segura "1 mensagem automática por
--     paciente por dia" junto com vaga, plano e voltar a chamar.
--
-- Sessão vendida e não usada é passivo, não lucro: nada aqui é apagado.
--
-- Aditiva e idempotente.

create table if not exists public.clinica_pacotes (
  id bigserial primary key,
  conta_id bigint not null references public.contas(id),
  plano_id bigint,                         -- de qual plano de tratamento nasceu
  prospeccao_id bigint references public.prospeccao(id) on delete set null,
  paciente_nome text not null,
  paciente_fone text not null default '',
  servico_id bigint not null,
  nome text not null,                      -- o nome do procedimento (a tela da recepção; nunca a mensagem)
  profissional_id bigint,
  sessoes_total smallint not null check (sessoes_total >= 1),
  sessoes_usadas smallint not null default 0 check (sessoes_usadas >= 0),
  intervalo_dias smallint not null default 21 check (intervalo_dias between 1 and 365),
  validade_ate date,
  estado text not null default 'ativo' check (estado in ('ativo','concluido','vencido','encerrado')),
  encerrado_motivo text,
  encerrado_por bigint,
  encerrado_em timestamptz,
  criado_em timestamptz not null default now(),
  atualizado_em timestamptz not null default now(),
  check (sessoes_usadas <= sessoes_total));
create index if not exists idx_clinica_pacotes_conta on public.clinica_pacotes (conta_id, estado);
create index if not exists idx_clinica_pacotes_lead on public.clinica_pacotes (conta_id, prospeccao_id);

create table if not exists public.clinica_pacote_consumos (
  id bigserial primary key,
  conta_id bigint not null references public.contas(id),
  pacote_id bigint not null references public.clinica_pacotes(id) on delete cascade,
  evento_id bigint not null unique,        -- um atendimento baixa uma sessão, uma vez
  criado_em timestamptz not null default now());

create table if not exists public.clinica_retornos (
  id bigserial primary key,
  conta_id bigint not null references public.contas(id),
  prospeccao_id bigint references public.prospeccao(id) on delete set null,
  evento_id bigint not null unique,        -- a consulta em que o médico pediu o retorno
  profissional_id bigint,
  paciente_nome text not null,
  paciente_fone text not null default '',
  vence_em date not null,
  estado text not null default 'aguardando' check (estado in ('aguardando','marcado','vencido','dispensado')),
  marcado_evento_id bigint,
  criado_em timestamptz not null default now(),
  atualizado_em timestamptz not null default now());
create index if not exists idx_clinica_retornos_conta on public.clinica_retornos (conta_id, estado, vence_em);

create table if not exists public.clinica_lembretes (
  id bigserial primary key,
  conta_id bigint not null references public.contas(id),
  conversa_id bigint not null,
  tipo text not null check (tipo in ('sessao','retorno','validade')),
  ref_id bigint not null,                  -- o pacote ou o retorno
  enviado_em timestamptz not null default now(),
  mensagem_id bigint);
create index if not exists idx_clinica_lembretes_conversa on public.clinica_lembretes (conta_id, conversa_id, enviado_em);

alter table public.clinica_agenda_config
  add column if not exists pacote_validade_meses smallint not null default 12,
  add column if not exists pacote_lembretes text not null default 'ligado',
  add column if not exists retorno_aviso_dias smallint not null default 7,
  add column if not exists pacote_bloqueia_atrasado boolean not null default false;

do $$ begin
  if not exists (select 1 from pg_constraint where conname = 'clinica_agenda_config_pacote_lembretes_check') then
    alter table public.clinica_agenda_config add constraint clinica_agenda_config_pacote_lembretes_check
      check (pacote_lembretes in ('off','ligado'));
  end if;
end $$;

-- rollback:
--   drop table if exists public.clinica_lembretes; drop table if exists public.clinica_retornos;
--   drop table if exists public.clinica_pacote_consumos; drop table if exists public.clinica_pacotes;
--   alter table public.clinica_agenda_config drop constraint if exists clinica_agenda_config_pacote_lembretes_check,
--     drop column if exists pacote_validade_meses, drop column if exists pacote_lembretes,
--     drop column if exists retorno_aviso_dias, drop column if exists pacote_bloqueia_atrasado;
