-- ============================================================
-- Migração 035 — Assinaturas do cliente (Fase 3, loja)
-- ============================================================
-- O cliente assina uma cesta (tamanho + frequência + restrições).
-- Reusa a tabela vinculos (Fase 0, cliente-cêntrico) como o elo
-- cliente–fornecedor, e adiciona os detalhes da assinatura.
-- Status começa em 'aguardando_pagamento' (Asaas entra na Fase 5).
-- Idempotente.
-- ============================================================

create table if not exists assinaturas (
    id            bigserial primary key,
    cliente_id    bigint not null references contas(id),
    fornecedor_id bigint not null references contas(id),
    tamanho_id    bigint not null references cesta_tamanhos(id),
    vinculo_id    bigint references vinculos(id),     -- elo cliente-cêntrico (Fase 0)
    frequencia    text not null default 'semanal'
                  check (frequencia in ('semanal','quinzenal','mensal')),
    dia_entrega   text,                               -- "quarta" (definido depois)
    status        text not null default 'aguardando_pagamento'
                  check (status in ('aguardando_pagamento','ativa','pausada','cancelada')),
    -- snapshot do que foi assinado (pra histórico mesmo se o tamanho mudar)
    preco_centavos bigint not null default 0,
    criado_em     timestamptz not null default now(),
    ativada_em    timestamptz,
    cancelada_em  timestamptz
);
create index if not exists ix_assin_cliente on assinaturas (cliente_id);
create index if not exists ix_assin_fornecedor on assinaturas (fornecedor_id);

-- restrições: produtos que o cliente NUNCA quer na cesta (a "lista de restrições")
create table if not exists assinatura_restricoes (
    id            bigserial primary key,
    assinatura_id bigint not null references assinaturas(id) on delete cascade,
    produto_id    bigint not null references catalogo_produtos(id)
);
create index if not exists ix_assin_restr on assinatura_restricoes (assinatura_id);
-- não duplicar a mesma restrição
create unique index if not exists ux_assin_restr
  on assinatura_restricoes (assinatura_id, produto_id);

-- ============================================================
-- ROLLBACK:
--   drop table if exists assinatura_restricoes;
--   drop table if exists assinaturas;
-- ============================================================
