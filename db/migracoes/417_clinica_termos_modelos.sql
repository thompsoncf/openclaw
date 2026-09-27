-- 417_clinica_termos_modelos.sql
-- Clínica, passo 0c-2b: os TERMOS DA CLÍNICA (mockup docs/mockups/clinica_prontuario.html,
-- seção 13, ideia 3 — "termos assinados no link: uso de dados, uso de imagem e termo do
-- procedimento, aceitos no celular com data, hora e cópia em PDF").
--
--   * `clinica_termos_modelos`: o texto que a clínica escreveu pro uso de dados (lgpd),
--     pro uso de imagem e pra cada PROCEDIMENTO (um por tipo de atendimento). Sem modelo,
--     vale o texto padrão do Zaq. Editar sobe a versão; o aceite guarda o texto que valia.
--   * `clinica_termos_aceites` passa a aceitar o termo 'procedimento', com o tipo de
--     atendimento e o agendamento a que se refere.
--
-- Aditiva e idempotente.

create table if not exists public.clinica_termos_modelos (
  id bigserial primary key,
  conta_id bigint not null references public.contas(id),
  chave text not null check (chave in ('lgpd','imagem','procedimento')),
  servico_id bigint,                          -- procedimento: o tipo de atendimento
  titulo text not null,
  texto text not null,
  versao integer not null default 1,
  ativo boolean not null default true,
  atualizado_por bigint,
  atualizado_em timestamptz not null default now(),
  criado_em timestamptz not null default now());
create unique index if not exists ux_clinica_termos_modelos_vivo
  on public.clinica_termos_modelos (conta_id, chave, coalesce(servico_id, 0)) where ativo;

alter table public.clinica_termos_aceites drop constraint if exists clinica_termos_aceites_termo_check;
alter table public.clinica_termos_aceites add constraint clinica_termos_aceites_termo_check
  check (termo in ('lgpd','imagem','procedimento'));
alter table public.clinica_termos_aceites
  add column if not exists servico_id bigint,
  add column if not exists evento_id bigint;

-- rollback:
--   alter table public.clinica_termos_aceites drop column if exists evento_id, drop column if exists servico_id;
--   delete from public.clinica_termos_aceites where termo = 'procedimento';
--   alter table public.clinica_termos_aceites drop constraint if exists clinica_termos_aceites_termo_check;
--   alter table public.clinica_termos_aceites add constraint clinica_termos_aceites_termo_check
--     check (termo in ('lgpd','imagem'));
--   drop table if exists public.clinica_termos_modelos;
