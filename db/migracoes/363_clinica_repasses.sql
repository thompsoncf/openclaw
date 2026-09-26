-- 363_clinica_repasses.sql
-- Fase 3b da clínica: o agente do WhatsApp PASSA PRA RECEPÇÃO o que não é dele
-- (finance/clinica_agente.py). Cada passagem é uma linha aqui e um item na tela
-- Hoje, "O agente passou pra você", até alguém responder a conversa ou apertar
-- Resolvido.
--
-- NADA DE DADO DE SAÚDE: guarda o MOTIVO (sintoma, foto, convênio...), nunca o
-- texto do paciente. Quem precisa ler abre a conversa, que já existe.
--
-- O agente não pausa a conversa ao passar (ele nunca se desliga sozinho): esta
-- tabela é só o recado pra recepção.
--
-- Aditiva e idempotente.

create table if not exists public.clinica_repasses (
  id bigserial primary key,
  conta_id bigint not null references public.contas(id),
  conversa_id bigint not null references public.conversas(id) on delete cascade,
  prospeccao_id bigint references public.prospeccao(id) on delete set null,
  motivo text not null check (motivo in
    ('sintoma','foto','audio','arquivo','desconto','convenio','urgencia','remarcar','marcar','pessoa')),
  criado_em timestamptz not null default now(),
  resolvido_em timestamptz,
  -- só registro (quem apertou Resolvido): sem chave estrangeira de propósito, pra
  -- que o botão nunca quebre por causa de uma sessão sem membro
  resolvido_por bigint);

create index if not exists idx_clinica_repasses_abertos
  on public.clinica_repasses (conta_id, criado_em) where resolvido_em is null;
create index if not exists idx_clinica_repasses_conversa
  on public.clinica_repasses (conversa_id, criado_em);

-- rollback:
--   drop table if exists public.clinica_repasses;
