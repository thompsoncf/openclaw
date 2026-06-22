-- ============================================================
-- Migração 036 — Cesta da semana (Fase 4, o montador)
-- ============================================================
-- O montador gera uma cesta por assinatura a cada ciclo. Guarda
-- aqui a cesta montada (cabeçalho + itens). Também adiciona a
-- margem alvo configurável do fornecedor. Idempotente.
-- ============================================================

-- margem alvo do fornecedor (até quanto do preço o custo pode ir)
-- default 60 = custo até 60% do preço (folga de 40% pra quebra+comissão+lucro)
alter table contas add column if not exists margem_alvo_pct numeric(5,2) default 60.00;

-- cabeçalho da cesta de uma semana (por assinatura)
create table if not exists cesta_semana (
    id            bigserial primary key,
    assinatura_id bigint not null references assinaturas(id),
    fornecedor_id bigint not null references contas(id),
    cliente_id    bigint not null references contas(id),
    data_entrega  date,
    status        text not null default 'sugerida'
                  check (status in ('sugerida','em_ajuste','confirmada','cobrada','entregue','cancelada')),
    preco_centavos bigint not null default 0,        -- o preço fixo da cesta (snapshot)
    custo_total_centavos bigint not null default 0,  -- soma do custo dos itens (privado)
    criada_em     timestamptz not null default now()
);
create index if not exists ix_cesta_sem_assin on cesta_semana (assinatura_id);
create index if not exists ix_cesta_sem_forn on cesta_semana (fornecedor_id);

-- itens da cesta montada
create table if not exists cesta_itens (
    id            bigserial primary key,
    cesta_id      bigint not null references cesta_semana(id) on delete cascade,
    produto_id    bigint not null references catalogo_produtos(id),
    grupo         text,                              -- fruta/legume/verdura/tempero
    quantidade    numeric(12,3) not null default 0,
    custo_unit_centavos bigint not null default 0,   -- snapshot do custo (privado)
    preco_unit_centavos bigint not null default 0    -- snapshot do preço de venda
);
create index if not exists ix_cesta_itens_cesta on cesta_itens (cesta_id);

-- ============================================================
-- ROLLBACK:
--   drop table if exists cesta_itens;
--   drop table if exists cesta_semana;
--   alter table contas drop column if exists margem_alvo_pct;
-- ============================================================
