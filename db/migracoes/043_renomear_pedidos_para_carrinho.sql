-- ============================================================
-- Migração 043 — Renomear "pedidos avulsos" para "carrinhos"
-- ============================================================
-- A migração 042 criou tabelas 'pedidos'/'pedido_itens' pro avulso, mas "pedido"
-- já significa CESTA da assinatura no sistema (finance/pedidos.py). Pra separar os
-- conceitos, o avulso vira 'carrinhos'/'carrinho_itens'. As colunas de config do
-- fornecedor (pedido_minimo, taxa_entrega) FICAM em contas (servem pros dois).
--
-- Como a 042 já foi aplicada e as tabelas estão VAZIAS (nenhum pedido real ainda),
-- o mais limpo é DROPAR as de 042 e criar as novas.
-- ============================================================

-- 1. dropar as tabelas da 042 (estão vazias — nenhum pedido avulso real ainda)
drop table if exists pedido_itens;
drop table if exists pedidos;

-- 2. criar as tabelas com o nome certo: carrinhos / carrinho_itens
create table if not exists carrinhos (
    id                bigint generated always as identity primary key,
    cliente_id        bigint not null references contas(id),
    fornecedor_id     bigint not null references contas(id),
    status            text not null default 'rascunho'
                      check (status in ('rascunho','aguardando','confirmado',
                                        'em_entrega','entregue','cancelado')),
    canal             text not null default 'web'
                      check (canal in ('web','whatsapp')),
    subtotal_centavos bigint not null default 0,
    taxa_centavos     bigint not null default 0,
    total_centavos    bigint not null default 0,
    endereco_entrega  text,
    obs               text,
    criado_em         timestamptz not null default now(),
    confirmado_em     timestamptz,
    entregue_em       timestamptz
);
create index if not exists ix_carrinhos_cliente on carrinhos (cliente_id);
create index if not exists ix_carrinhos_fornecedor on carrinhos (fornecedor_id, status);

create table if not exists carrinho_itens (
    id                bigint generated always as identity primary key,
    carrinho_id       bigint not null references carrinhos(id) on delete cascade,
    produto_id        bigint not null references catalogo_produtos(id),
    nome              text not null,
    unidade           text not null default 'kg',
    quantidade        numeric(12,3) not null default 1,
    preco_unit_centavos bigint not null default 0,
    total_centavos    bigint not null default 0
);
create index if not exists ix_carrinho_itens_carrinho on carrinho_itens (carrinho_id);

-- 3. as colunas de config (pedido_minimo_centavos, taxa_entrega_centavos) em contas
--    JÁ foram criadas na 042 e FICAM (servem pra carrinho avulso).
alter table contas add column if not exists pedido_minimo_centavos bigint not null default 0;
alter table contas add column if not exists taxa_entrega_centavos   bigint not null default 0;

-- ============================================================
-- ROLLBACK:
--   drop table if exists carrinho_itens;
--   drop table if exists carrinhos;
-- ============================================================
