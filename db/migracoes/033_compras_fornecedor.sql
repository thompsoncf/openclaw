-- ============================================================
-- Migração 033 — Módulo de Compras do fornecedor
-- ============================================================
-- Uma COMPRA é o cabeçalho que agrupa vários itens de entrada
-- (uma ida ao CEASA). Ao confirmar, gera as movimentações de
-- entrada (estoque_mov) de cada item. Idempotente.
--
-- ⚠️ PRIVACIDADE: o custo aqui é do FORNECEDOR (segredo dele).
-- NUNCA cruzar com precos_observados (banco de preços = preço de
-- VENDA público, de cupom de cliente). São mundos separados.
-- ============================================================

-- ---- 1. COMPRA (cabeçalho) ----
create table if not exists compras_fornecedor (
    id            bigserial primary key,
    fornecedor_id bigint not null references contas(id),
    origem_id     bigint references origem_compra(id),    -- de quem comprou (CEASA...)
    data_compra   date not null default current_date,
    total_centavos bigint not null default 0,             -- soma dos itens (conferência)
    chave_nfe     text,                                   -- chave da NF-e, se veio de nota
    fonte         text not null default 'manual'          -- 'manual' | 'nota_pdf' | 'nota_foto' | 'nota_qr'
                  check (fonte in ('manual','nota_pdf','nota_foto','nota_qr')),
    status        text not null default 'rascunho'         -- rascunho (revisando) -> confirmada
                  check (status in ('rascunho','confirmada','cancelada')),
    criado_em     timestamptz not null default now(),
    confirmada_em timestamptz
);
create index if not exists ix_compras_fornecedor on compras_fornecedor (fornecedor_id);
-- evita importar a mesma nota 2x (idempotência por chave, por fornecedor)
create unique index if not exists ux_compras_chave
  on compras_fornecedor (fornecedor_id, chave_nfe) where chave_nfe is not null;

-- ---- 2. ITENS da compra (o que veio na nota / o que ele digitou) ----
-- enquanto a compra é 'rascunho', os itens são editáveis (revisão). Ao confirmar,
-- cada item vira uma movimentação de entrada em estoque_mov.
create table if not exists compras_itens (
    id            bigserial primary key,
    compra_id     bigint not null references compras_fornecedor(id) on delete cascade,
    produto_id    bigint references catalogo_produtos(id),  -- null até casar/criar o produto
    descricao     text not null,                            -- o que veio na nota (texto cru)
    quantidade    numeric(12,3) not null default 0,
    unidade       text default 'kg',
    custo_unit_centavos bigint not null default 0,
    casado        boolean not null default false            -- já vinculado a um produto?
);
create index if not exists ix_compras_itens_compra on compras_itens (compra_id);

-- ============================================================
-- ROLLBACK:
--   drop table if exists compras_itens;
--   drop table if exists compras_fornecedor;
-- ============================================================
