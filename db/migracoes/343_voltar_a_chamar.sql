-- 343_voltar_a_chamar.sql
-- "Voltar a chamar depois do preço" — o primeiro pedaço do nicho clínica que mexe
-- em dinheiro medido. Na Espaço Pelle (conta 39), de 24/08 a 23/09/2026, 11
-- pacientes escreveram, receberam o preço da consulta (R$ 500) e só 1 marcou. Os
-- outros não foram chamados de novo. Ver finance/voltar_a_chamar.py.
--
-- TRÊS TABELAS, TODAS POR CONTA:
--   voltar_a_chamar_config    o modo da conta (off · sugere · ligado) e o que o
--                             dono mudou em relação ao padrão do perfil. Sem linha
--                             = 'off', que é como toda conta nasce.
--   voltar_a_chamar_toques    um toque por linha: +3h, +1 dia, +3 dias, +7 dias
--                             depois do preço (1 a 4), ou a repescagem (0) de quem
--                             ficou parado antes da conta ligar.
--   voltar_a_chamar_bloqueios quem nunca mais recebe toque: "não é paciente"
--                             (fornecedor, contador, banco) e quem pediu pra sair.
--                             Por telefone (últimos 8 dígitos), não por lead: o
--                             mesmo número volta como lead novo e continua fora.
--
-- SEM `on delete cascade`: apagar um lead não pode apagar o histórico do que foi
-- mandado pra ele — é o que responde "quem mandou isto?" quando o paciente reclama.
--
-- Aditiva e idempotente.

create table if not exists public.voltar_a_chamar_config (
  conta_id bigint primary key references public.contas(id),
  modo text not null default 'off' check (modo in ('off','sugere','ligado')),
  toques_min text,          -- null = herda do perfil
  textos jsonb,             -- null = herda do perfil
  teto_dia int,             -- null = herda do perfil
  ligado_em timestamptz,    -- a 1ª vez que saiu do 'off': divide repescagem de toque
  atualizado_em timestamptz not null default now());

create table if not exists public.voltar_a_chamar_toques (
  id bigserial primary key,
  conta_id bigint not null references public.contas(id),
  -- o card é opcional: as 84 conversas que a conta 39 trouxe ao conectar o WhatsApp
  -- (23/09/2026) entraram sem card, e 8 dos 11 pacientes que receberam o preço
  -- estão entre elas. A sequência é da CONVERSA; o card, quando existe, é lido de
  -- `conversas.prospeccao_id` na hora.
  prospeccao_id bigint references public.prospeccao(id),
  conversa_id bigint not null,
  preco_msg_id bigint not null,
  toque smallint not null check (toque between 0 and 4),   -- 0 = repescagem
  estado text not null check (estado in ('pendente','enviado','falhou','pulado',
         'dispensado','nao_paciente','saiu','marcou')),
  devido_em timestamptz not null,
  texto text,
  enviado_em timestamptz,
  enviado_por text check (enviado_por in ('agente','recepcao')),
  membro_id bigint,
  mensagem_id bigint,
  criado_em timestamptz not null default now(),
  unique (conta_id, conversa_id, preco_msg_id, toque));

create index if not exists idx_vac_toques_fila
  on public.voltar_a_chamar_toques (conta_id, estado, devido_em);
create index if not exists idx_vac_toques_conversa
  on public.voltar_a_chamar_toques (conversa_id);

create table if not exists public.voltar_a_chamar_bloqueios (
  id bigserial primary key,
  conta_id bigint not null references public.contas(id),
  numero8 text not null,
  motivo text not null check (motivo in ('nao_paciente','saiu')),
  membro_id bigint,
  criado_em timestamptz not null default now(),
  unique (conta_id, numero8));

-- rollback:
--   drop table if exists public.voltar_a_chamar_bloqueios;
--   drop table if exists public.voltar_a_chamar_toques;
--   drop table if exists public.voltar_a_chamar_config;
