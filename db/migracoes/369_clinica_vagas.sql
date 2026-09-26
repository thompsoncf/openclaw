-- 369_clinica_vagas.sql
-- Fase 4 da clínica, primeira parte: a VAGA LIBERADA (o "motor de ocupação" de
-- docs/mockups/clinica_agenda_itinerante.html, seção 04). Horário que abre por
-- cancelamento vai pra quem cabe nele, e fica com quem responder "1" primeiro
-- (finance/clinica_vagas.py, tela /painel/clinica/vagas).
--
-- Oferta é mensagem que o paciente não pediu: no 1º mês a recepção aprova cada
-- horário (vagas_modo = 'aprova', o padrão); 'auto' manda sozinho; 'off' nem
-- procura. A mensagem nunca diz o procedimento, e todo convite ensina PARAR.
--
-- Nada de dado de saúde: quem, por que foi chamado (grupo e uma frase de agenda
-- ou funil), e o que respondeu.
--
-- Aditiva e idempotente.

alter table public.clinica_agenda_config
  add column if not exists vagas_modo text not null default 'aprova',
  add column if not exists vagas_teto_dia smallint not null default 20;

do $$ begin
  if not exists (select 1 from pg_constraint where conname = 'clinica_agenda_config_vagas_modo_check') then
    alter table public.clinica_agenda_config add constraint clinica_agenda_config_vagas_modo_check
      check (vagas_modo in ('off','aprova','auto'));
  end if;
end $$;

create table if not exists public.clinica_vagas (
  id bigserial primary key,
  conta_id bigint not null references public.contas(id),
  profissional_id bigint not null,
  inicio timestamptz not null,
  fim timestamptz not null,
  clinica_local_id bigint,
  origem_evento_id bigint,               -- a consulta cancelada que abriu o horário
  estado text not null default 'aguardando' check (estado in
    ('aguardando',     -- esperando a recepção aprovar (ou a janela abrir, no automático)
     'oferta',         -- convite na rua, até oferta_ate
     'preenchida',     -- alguém respondeu 1 e foi marcado
     'livre',          -- ninguém pegou, ou a recepção parou: fica livre na agenda
     'balcao',         -- menos de 1h de aviso: não manda, a recepção oferece no balcão
     'ocupada')),      -- alguém marcou o horário por fora (recepção, agente)
  rodada smallint not null default 0,
  aprovada_em timestamptz,
  aprovada_por bigint,
  oferta_ate timestamptz,
  preenchida_evento_id bigint,
  preenchida_em timestamptz,
  criado_em timestamptz not null default now(),
  atualizado_em timestamptz not null default now(),
  unique (conta_id, profissional_id, inicio));

create index if not exists idx_clinica_vagas_abertas
  on public.clinica_vagas (conta_id, estado, inicio);

create table if not exists public.clinica_vaga_ofertas (
  id bigserial primary key,
  conta_id bigint not null references public.contas(id),
  vaga_id bigint not null references public.clinica_vagas(id) on delete cascade,
  conversa_id bigint not null,
  prospeccao_id bigint,
  nome text not null,
  fone text not null,
  servico_id bigint not null,            -- o atendimento que ela marcaria (consulta, retorno)
  grupo text not null check (grupo in ('pediu','vem','preco','retorno')),
  porque text not null,                  -- uma frase de agenda/funil, nunca de saúde
  rodada smallint not null,
  estado text not null default 'enviada' check (estado in
    ('enviada','ganhou','perdeu','recusou','parar','falhou','expirou')),
  enviada_em timestamptz not null default now(),
  respondida_em timestamptz,
  avisada_em timestamptz,                -- "acabou de ser preenchida" já foi dito
  mensagem_id bigint,
  unique (vaga_id, conversa_id));

create index if not exists idx_clinica_vaga_ofertas_conversa
  on public.clinica_vaga_ofertas (conta_id, conversa_id, enviada_em);

-- rollback:
--   drop table if exists public.clinica_vaga_ofertas;
--   drop table if exists public.clinica_vagas;
--   alter table public.clinica_agenda_config drop constraint if exists clinica_agenda_config_vagas_modo_check,
--     drop column if exists vagas_modo, drop column if exists vagas_teto_dia;
