-- 392_ia_orcamento.sql
-- ETAPA 3 DO VENDEDOR IA (mockup docs/mockups/vendedor_ia_chip.html, versão 5): a IA
-- da regra por número MONTA o orçamento, alguém da equipe CONFERE com um toque antes
-- de ir pro cliente ("o ideal é sempre dar valor aproximado e deixar alguém conferir
-- antes de fechar até a IA ficar 100% treinada" — dono, 26/09), e depois da aprovação
-- a data fica SEGURADA por 72h esperando o sinal (orçamento de vendedor segue com a
-- regra de sempre).
--
-- 1. Na regra do chip: a chave, quem confere, o sinal (%), a validade e a reserva.
-- 2. `ia_orcamentos`: um por orçamento que a IA montou — a fila da conferência e o
--    relógio do sinal (a mensagem da aprovação, os lembretes de 24h e 48h, a data
--    liberada, a data confirmada) e o comprovante que chegou.
--
-- Não toca em `canais_config` nem em nada da conexão (CLAUDE.md §1). Tabela à parte,
-- e não colunas em `orcamentos`, pra não pedir trava em tabela viva. Aditiva e
-- idempotente.

alter table public.chip_regra
  add column if not exists orc_ia            boolean  not null default false,
  add column if not exists orc_conferente_id bigint   references public.membros(id) on delete set null,
  add column if not exists orc_sinal_pct     smallint not null default 30,
  add column if not exists orc_validade_dias smallint not null default 7,
  add column if not exists orc_reserva_h     smallint not null default 72;

create table if not exists public.ia_orcamentos (
  orcamento_id      bigint primary key,
  conta_id          bigint not null references public.contas(id) on delete restrict,
  prospeccao_id     bigint,
  conversa_id       bigint,
  -- conferir → enviado (alguém conferiu e mandou) | descartado
  estado            text not null default 'conferir',
  conferido_por     bigint,
  conferido_em      timestamptz,
  -- o relógio do sinal (cada passo sai uma vez)
  aprovado_msg_em   timestamptz,     -- "aprovou! o sinal é R$ X, a data fica segurada até…"
  lembrete_24_em    timestamptz,
  lembrete_48_em    timestamptz,
  liberada_msg_em   timestamptz,     -- a reserva venceu sem sinal: o cliente é avisado
  confirmada_msg_em timestamptz,     -- o sinal foi confirmado: "data confirmada!"
  comprovante_em    timestamptz,     -- chegou foto/documento depois da aprovação
  bloqueio          text,            -- a data já tinha festa: não segurou, a equipe decide
  envio_falhas      smallint not null default 0,
  envio_falhou_em   timestamptz,
  criado_em         timestamptz not null default now(),
  constraint ia_orcamentos_estado_check check (estado in ('conferir','enviado','descartado'))
);
create index if not exists ia_orcamentos_conta_idx on public.ia_orcamentos (conta_id, estado);
create index if not exists ia_orcamentos_lead_idx on public.ia_orcamentos (prospeccao_id);

-- rollback:
--   drop table if exists public.ia_orcamentos;
--   alter table public.chip_regra drop column if exists orc_ia,
--     drop column if exists orc_conferente_id, drop column if exists orc_sinal_pct,
--     drop column if exists orc_validade_dias, drop column if exists orc_reserva_h;
