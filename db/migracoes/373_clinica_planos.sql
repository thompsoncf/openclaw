-- 373_clinica_planos.sql
-- Fase 5 da clínica: o PLANO DE TRATAMENTO (docs/mockups/clinica_planos_pacotes_assinatura.html,
-- seções 03 e 11, passos 1 e 2). Depois da consulta, a recepção monta a proposta do
-- que o médico indicou — procedimentos, sessões, desconto e formas de pagamento —,
-- manda no WhatsApp com o link, e o Zaq cobra a decisão (D+1, D+3; a recepção é
-- avisada na véspera de vencer). Aceito, o card vai pra Fechado e as parcelas viram
-- títulos a receber (finance/clinica_planos.py, telas em /painel/clinica/planos e a
-- página pública /plano/{token}).
--
-- POR QUE UMA TABELA DA CLÍNICA, e não um terceiro modo em `orcamentos`: pelo mesmo
-- motivo da reforma (355). A proposta de hoje tem dois modos, evento e recorrente,
-- e cada um puxa página, contrato, funil e títulos que todas as contas usam. O que
-- o plano precisa do resto do sistema — o título a receber — é o título comum.
--
-- A PROPOSTA NÃO É PRONTUÁRIO: o nome do procedimento e o valor, nunca diagnóstico
-- ou queixa. Nada de promessa de resultado.
--
-- Aditiva e idempotente.

create table if not exists public.clinica_planos (
  id bigserial primary key,
  conta_id bigint not null references public.contas(id),
  prospeccao_id bigint references public.prospeccao(id) on delete set null,
  evento_id bigint,                        -- a consulta de onde saiu (a do "o médico propôs tratamento?")
  profissional_id bigint,
  paciente_nome text not null,
  paciente_fone text not null default '',
  -- [{servico_id|null, nome, sessoes, valor_unit_centavos}]
  itens jsonb not null default '[]'::jsonb,
  subtotal_centavos bigint not null default 0 check (subtotal_centavos >= 0),
  desconto_pct numeric(5,2) not null default 0 check (desconto_pct >= 0 and desconto_pct <= 100),
  total_centavos bigint not null default 0 check (total_centavos >= 0),
  pix_desconto_pct numeric(5,2) not null default 0 check (pix_desconto_pct >= 0 and pix_desconto_pct <= 50),
  cartao_parcelas smallint not null default 4 check (cartao_parcelas between 1 and 24),
  parcelado boolean not null default true,  -- entrada + parcelas mensais (boleto/Pix)
  validade_ate date,
  status text not null default 'rascunho' check (status in
    ('rascunho','aguardando_aprovacao','enviado','aceito','recusado','vencido','cancelado')),
  desconto_aprovado_por bigint,
  desconto_aprovado_em timestamptz,
  token text unique,
  enviado_em timestamptz,
  mensagem_id bigint,
  visto_em timestamptz,                    -- a 1ª vez que o link foi aberto
  aceito_em timestamptz,
  aceito_forma text check (aceito_forma is null or aceito_forma in ('pix','cartao','parcelado')),
  aceito_nome text,
  aceito_ip text,
  aceito_por text,                         -- 'link' | 'whatsapp' | 'recepcao'
  titulos jsonb not null default '[]'::jsonb,
  -- a cobrança da decisão: quando saiu cada toque (D+1, D+3) e o aviso da véspera
  toque1_em timestamptz,
  toque3_em timestamptz,
  aviso_vespera_em timestamptz,
  criado_por bigint,
  criado_em timestamptz not null default now(),
  atualizado_em timestamptz not null default now());

create index if not exists idx_clinica_planos_conta on public.clinica_planos (conta_id, status, enviado_em);
create index if not exists idx_clinica_planos_lead on public.clinica_planos (conta_id, prospeccao_id);

alter table public.clinica_agenda_config
  add column if not exists planos_teto_desconto_pct numeric(5,2) not null default 10,
  add column if not exists planos_pix_desconto_pct numeric(5,2) not null default 0,
  add column if not exists planos_cartao_parcelas smallint not null default 4,
  add column if not exists planos_validade_dias smallint not null default 7,
  add column if not exists planos_cobranca text not null default 'ligado';

do $$ begin
  if not exists (select 1 from pg_constraint where conname = 'clinica_agenda_config_planos_cobranca_check') then
    alter table public.clinica_agenda_config add constraint clinica_agenda_config_planos_cobranca_check
      check (planos_cobranca in ('off','ligado'));
  end if;
end $$;

-- rollback:
--   drop table if exists public.clinica_planos;
--   alter table public.clinica_agenda_config drop constraint if exists clinica_agenda_config_planos_cobranca_check,
--     drop column if exists planos_teto_desconto_pct, drop column if exists planos_pix_desconto_pct,
--     drop column if exists planos_cartao_parcelas, drop column if exists planos_validade_dias,
--     drop column if exists planos_cobranca;
