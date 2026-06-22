-- ============================================================
-- Migração 032 — FASE 1 Zaq Fornecedor: catálogo + estoque
-- ============================================================
-- Cria: produtos do fornecedor, movimentações de estoque
-- (entrada/saída/perda) e origem da compra (de quem ele compra).
-- Idempotente. Saldo e custo médio são CALCULADOS das movimentações.
-- ============================================================

-- ---- 1. ORIGEM DA COMPRA (de quem o fornecedor compra: CEASA, produtor...) ----
-- cadastro leve: só nome + contato opcional. NÃO confundir com o fornecedor (que é
-- a conta no Zaq). "origem" = de quem ELE compra pra revender.
create table if not exists origem_compra (
    id            bigserial primary key,
    fornecedor_id bigint not null references contas(id),  -- a quem essa origem pertence
    nome          text not null,                          -- "CEASA", "Sítio do João"
    contato       text,                                   -- opcional
    ativo         boolean not null default true,
    criado_em     timestamptz not null default now()
);
create index if not exists ix_origem_fornecedor on origem_compra (fornecedor_id);

-- ---- 2. PRODUTOS do catálogo do fornecedor ----
-- custo_medio e saldo são CALCULADOS das movimentações (guardados aqui como cache
-- pra leitura rápida; a verdade é a soma das movimentações).
create table if not exists catalogo_produtos (
    id            bigserial primary key,
    fornecedor_id bigint not null references contas(id),
    nome          text not null,                 -- "Tomate", "Banana prata"
    unidade       text not null default 'kg'     -- kg | duzia | unidade | maco | bandeja
                  check (unidade in ('kg','duzia','unidade','maco','bandeja','litro','pacote')),
    categoria     text,                          -- fruta | verdura | legume | tempero | outro
    preco_venda_centavos  bigint not null default 0,   -- preço ao cliente
    custo_medio_centavos  bigint not null default 0,   -- CMP, recalculado a cada entrada
    saldo         numeric(12,3) not null default 0,    -- saldo atual (das movimentações)
    estoque_minimo numeric(12,3) not null default 0,   -- dispara alerta de reposição
    disponivel    boolean not null default true,       -- liga/desliga (acabou hoje)
    ativo         boolean not null default true,        -- soft delete
    criado_em     timestamptz not null default now(),
    atualizado_em timestamptz not null default now()
);
create index if not exists ix_catalogo_fornecedor on catalogo_produtos (fornecedor_id);
create index if not exists ix_catalogo_disp on catalogo_produtos (fornecedor_id, disponivel) where ativo;

-- ---- 3. MOVIMENTAÇÕES de estoque (entrada / saída / perda) ----
-- o saldo do produto = soma das entradas – saídas – perdas. Esta tabela É a VERDADE.
create table if not exists estoque_mov (
    id            bigserial primary key,
    produto_id    bigint not null references catalogo_produtos(id),
    fornecedor_id bigint not null references contas(id),   -- redundante p/ query rápida
    tipo          text not null
                  check (tipo in ('entrada','saida','perda','ajuste')),
    quantidade    numeric(12,3) not null,                  -- sempre positiva; tipo define sinal
    custo_unit_centavos bigint,                            -- só em entrada (preço de compra)
    origem_id     bigint references origem_compra(id),     -- só em entrada (de quem comprou)
    motivo        text,                                    -- "estragou", "pedido #123", ajuste
    criado_em     timestamptz not null default now()
);
create index if not exists ix_mov_produto on estoque_mov (produto_id);
create index if not exists ix_mov_fornecedor on estoque_mov (fornecedor_id);
create index if not exists ix_mov_origem on estoque_mov (origem_id) where origem_id is not null;

-- ============================================================
-- ROLLBACK (rodar manual, com cuidado):
--   drop table if exists estoque_mov;
--   drop table if exists catalogo_produtos;
--   drop table if exists origem_compra;
-- ============================================================
