-- 047_pagamento_pedido.sql
-- Pagamento do pedido da loja: forma escolhida no checkout, link Asaas e status.

alter table carrinhos add column if not exists forma_pagamento text;
-- valores usados: 'entrega_pix' | 'entrega_cartao' | 'entrega_dinheiro' | 'pagar_agora'

alter table carrinhos add column if not exists pagamento_link_url text;
alter table carrinhos add column if not exists pago boolean not null default false;
alter table carrinhos add column if not exists pago_em timestamptz;
