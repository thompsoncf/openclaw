-- 409_clinica_ficha_link.sql
-- Clínica, passo 0c do prontuário: a ficha que nasce no agendamento
-- (docs/mockups/clinica_prontuario.html, seções 12 e 13 — aprovado pelo dono em
-- 26/09/2026). finance/clinica_ficha_link.py, finance/clinica_preconsulta.py e a
-- página pública /ficha/{token} (web/ficha_publica.py).
--
--   * O LINK DA FICHA: cada paciente tem um token (o mesmo link na confirmação e na
--     véspera). A página abre com a data de nascimento; 5 erros travam por 30 minutos.
--   * A PRÉ-CONSULTA: o que o paciente conta antes da consulta (queixa, alergia,
--     remédios, gravidez). É conteúdo CLÍNICO: só o profissional de saúde lê. A
--     recepção vê só que foi respondida e se há alergia (`tem_alergia`, sem o texto).
--     O agente do WhatsApp nunca lê esta tabela (tests/test_clinica_ficha_link.py trava).
--   * OS TERMOS: uso de dados (LGPD) e de imagem, aceitos no link com nome, data, hora,
--     IP e o texto exato que a pessoa leu (o texto muda; o aceite guarda o que valia).
--   * `clinica_agenda_config.ficha_link`: a clínica liga o link depois de ler os termos.
--     Nasce desligado.
--
-- Aditiva e idempotente.

alter table public.clientes
  add column if not exists ficha_token text,
  add column if not exists ficha_tentativas smallint not null default 0,
  add column if not exists ficha_travada_ate timestamptz,
  add column if not exists ficha_aberta_em timestamptz;
create unique index if not exists ux_clientes_ficha_token on public.clientes (ficha_token)
  where ficha_token is not null;

create table if not exists public.clinica_preconsultas (
  id bigserial primary key,
  conta_id bigint not null references public.contas(id),
  cliente_id bigint not null references public.clientes(id) on delete cascade,
  evento_id bigint references public.eventos_agenda(id) on delete set null,
  curta boolean not null default false,        -- a do retorno: "mudou algo?"
  respostas jsonb not null default '{}'::jsonb,
  tem_alergia boolean not null default false,  -- o aviso da recepção, sem o texto clínico
  respondida_por text not null default 'paciente' check (respondida_por in ('paciente','responsavel')),
  criado_em timestamptz not null default now());
create index if not exists clinica_preconsultas_conta_cliente
  on public.clinica_preconsultas (conta_id, cliente_id, criado_em desc);

create table if not exists public.clinica_termos_aceites (
  id bigserial primary key,
  conta_id bigint not null references public.contas(id),
  cliente_id bigint not null references public.clientes(id) on delete cascade,
  termo text not null check (termo in ('lgpd','imagem')),
  opcao text,                                  -- imagem: 'clinico' | 'divulgacao' | 'nao'
  titulo text not null,
  texto text not null,                         -- o que a pessoa leu, exatamente
  versao text not null,
  aceito_por_nome text not null,
  papel text not null default 'paciente' check (papel in ('paciente','responsavel')),
  ip text,
  user_agent text,
  aceito_em timestamptz not null default now());
create index if not exists clinica_termos_aceites_conta_cliente
  on public.clinica_termos_aceites (conta_id, cliente_id, termo, aceito_em desc);

alter table public.clinica_agenda_config
  add column if not exists ficha_link text not null default 'off';

-- rollback:
--   alter table public.clinica_agenda_config drop column if exists ficha_link;
--   drop table if exists public.clinica_termos_aceites; drop table if exists public.clinica_preconsultas;
--   drop index if exists ux_clientes_ficha_token;
--   alter table public.clientes drop column if exists ficha_aberta_em, drop column if exists ficha_travada_ate,
--     drop column if exists ficha_tentativas, drop column if exists ficha_token;
