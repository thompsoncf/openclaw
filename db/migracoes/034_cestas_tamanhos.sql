-- ============================================================
-- Migração 034 — Tamanhos de cesta (Fase 2, lado do cliente)
-- ============================================================
-- O fornecedor define os tamanhos de cesta (P/M/G) com PREÇO FIXO
-- e o "esqueleto" (quantas porções de cada grupo). A composição
-- exata varia toda semana (o montador escolhe), mas o tamanho e o
-- preço são fixos — é o que o cliente assina. Idempotente.
-- ============================================================

create table if not exists cesta_tamanhos (
    id            bigserial primary key,
    fornecedor_id bigint not null references contas(id),
    nome          text not null,                  -- "Pequena", "Média", "Grande"
    slug          text not null,                  -- "p", "m", "g" (ou livre)
    preco_centavos bigint not null default 0,     -- PREÇO FIXO da cesta
    -- esqueleto: quantas porções de cada grupo (o equilíbrio da cesta)
    qtd_frutas    int not null default 0,
    qtd_legumes   int not null default 0,
    qtd_verduras  int not null default 0,
    qtd_temperos  int not null default 0,
    descricao     text,                           -- "ideal pra 2 pessoas/semana"
    ativo         boolean not null default true,
    ordem         int not null default 0,         -- ordem de exibição (P<M<G)
    criado_em     timestamptz not null default now(),
    atualizado_em timestamptz not null default now()
);
create index if not exists ix_cesta_tam_fornecedor on cesta_tamanhos (fornecedor_id);
-- nome único por fornecedor (não duplicar "Média")
create unique index if not exists ux_cesta_tam_slug
  on cesta_tamanhos (fornecedor_id, slug);

-- ============================================================
-- ROLLBACK:
--   drop table if exists cesta_tamanhos;
-- ============================================================
