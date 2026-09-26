-- 394_ia_uso.sql
-- ETAPA 4 DO VENDEDOR IA: o custo da IA, por conversa e por lead (mockup
-- docs/mockups/vendedor_ia_chip.html, painel do desafio: "custo da IA — por lead e por
-- contrato").
--
-- Até aqui NADA registrava o custo da IA que atende o WhatsApp: `Brain.chamar` só
-- guarda o uso em memória, e o agente nunca o lia. `uso_api` (migração 024) é do
-- assistente do Telegram e não sabe de conversa nem de lead. Esta tabela é o livro da
-- IA vendedora: uma linha por chamada, com os tokens, o modelo e o custo em centavos
-- de real, pela mesma régua de preço que `core/agent.py` já usa.
--
-- Não toca em nada vivo: tabela nova, só insert. Aditiva e idempotente.

create table if not exists public.ia_uso (
  id               bigserial primary key,
  conta_id         bigint not null references public.contas(id) on delete restrict,
  conversa_id      bigint,
  prospeccao_id    bigint,
  modelo           text,
  input_tokens     integer not null default 0,
  cache_read_tokens  integer not null default 0,
  cache_write_tokens integer not null default 0,
  output_tokens    integer not null default 0,
  custo_centavos   integer not null default 0,
  criado_em        timestamptz not null default now()
);
create index if not exists ia_uso_conta_idx on public.ia_uso (conta_id, criado_em desc);
create index if not exists ia_uso_lead_idx on public.ia_uso (prospeccao_id);

-- rollback:
--   drop table if exists public.ia_uso;
