-- ============================================================
-- Migração 042 — Pedidos avulsos (carrinho / delivery)
-- ============================================================
-- Além da assinatura de cesta, o cliente pode montar um PEDIDO avulso:
-- escolhe produtos do catálogo item a item, fecha o pedido, o fornecedor
-- confirma e cobra (fluxo de delivery). Web e WhatsApp.
-- Reusa catalogo_produtos (preco_venda_centavos já existe).
-- Idempotente.
-- ============================================================

-- config de delivery por fornecedor (mínimo do pedido + taxa de entrega)
alter table contas add column if not exists pedido_minimo_centavos bigint not null default 0;
alter table contas add column if not exists taxa_entrega_centavos   bigint not null default 0;

-- ------------------------------------------------------------
-- PEDIDO: um pedido avulso de um cliente a um fornecedor
-- ------------------------------------------------------------
create table if not exists pedidos (
    id                bigint generated always as identity primary key,
    cliente_id        bigint not null references contas(id),
    fornecedor_id     bigint not null references contas(id),
    -- rascunho: cliente ainda montando | aguardando: enviado, fornecedor vê
    -- confirmado: fornecedor aceitou (aí cobra) | em_entrega | entregue | cancelado
    status            text not null default 'rascunho'
                      check (status in ('rascunho','aguardando','confirmado',
                                        'em_entrega','entregue','cancelado')),
    canal             text not null default 'web'   -- web | whatsapp
                      check (canal in ('web','whatsapp')),
    subtotal_centavos bigint not null default 0,    -- soma dos itens (cache)
    taxa_centavos     bigint not null default 0,    -- taxa de entrega (snapshot)
    total_centavos    bigint not null default 0,    -- subtotal + taxa (cache)
    endereco_entrega  text,                          -- pra onde entregar
    obs               text,                          -- observação do cliente
    criado_em         timestamptz not null default now(),
    confirmado_em     timestamptz,
    entregue_em       timestamptz
);
create index if not exists ix_pedidos_cliente on pedidos (cliente_id);
create index if not exists ix_pedidos_fornecedor on pedidos (fornecedor_id, status);

-- ------------------------------------------------------------
-- PEDIDO_ITENS: os produtos do pedido (snapshot de nome e preço)
-- ------------------------------------------------------------
create table if not exists pedido_itens (
    id                bigint generated always as identity primary key,
    pedido_id         bigint not null references pedidos(id) on delete cascade,
    produto_id        bigint not null references catalogo_produtos(id),
    -- snapshot: nome e preço no momento do pedido (preço pode mudar depois)
    nome              text not null,
    unidade           text not null default 'kg',
    quantidade        numeric(12,3) not null default 1,
    preco_unit_centavos bigint not null default 0,        -- preço por unidade (snapshot)
    total_centavos    bigint not null default 0           -- quantidade * preco_unit (cache)
);
create index if not exists ix_pedido_itens_pedido on pedido_itens (pedido_id);

-- ============================================================
-- ROLLBACK:
--   drop table if exists pedido_itens;
--   drop table if exists pedidos;
--   alter table contas drop column if exists pedido_minimo_centavos;
--   alter table contas drop column if exists taxa_entrega_centavos;
-- ============================================================
