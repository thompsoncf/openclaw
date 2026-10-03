-- 670_obra_pedidos_cd.sql
-- O CD (almoxarifado central) das obras — PR 1 de 3 do desenho
-- docs/mockups/obras_cd_almoxarifado.html, aprovado pelo dono em 03/10/2026
-- ("segue as recomendações"): a obra PEDE pelo app do mestre, o CD SEPARA e
-- despacha, e o mestre confirma o RECEBI com foto — que é o que move o material
-- do CD pra obra.
--
--   obra_pedidos       o pedido: de qual obra, quem pediu, se é urgente, pra
--                      quando, o recado, e cada passo (pedido → separando → saiu
--                      → recebido, ou cancelado) com quem e quando
--   obra_pedido_itens  o que foi pedido e o que chegou de verdade; os ids dos
--                      movimentos do estoque que o "recebi" gravou
--
-- O ESTOQUE NÃO MUDA: o "recebi" grava a mesma transferência pareada do "levei"
-- (estoque_mov, migração 484) — o pedido só dá dono, hora e prova ao movimento.
-- Decisões do dono: sem aprovação (o CD separa direto); o "recebi" é a prova.
--
-- NÚMERO 670: o maior em uso em main + PRs abertos era 640 — pulo pra longe.
--
-- Aditiva e idempotente.

create table if not exists public.obra_pedidos (
  id             bigserial primary key,
  conta_id       bigint not null references public.contas(id) on delete cascade,
  obra_id        bigint not null references public.obras(id) on delete cascade,
  pedido_por     bigint references public.membros(id) on delete set null,
  urgente        boolean not null default false,
  prazo          date,
  recado         text not null default '',
  status         text not null default 'pedido'
                 check (status in ('pedido', 'separando', 'saiu', 'recebido', 'cancelado')),
  criado_em      timestamptz not null default now(),
  separando_em   timestamptz,
  saiu_em        timestamptz,
  recebido_em    timestamptz,
  recebido_por   bigint references public.membros(id) on delete set null,
  foto_id        bigint references public.obra_fotos(id) on delete set null,
  cancelado_em   timestamptz
);
create index if not exists idx_obra_pedidos_conta on public.obra_pedidos (conta_id, status, criado_em desc);

create table if not exists public.obra_pedido_itens (
  id                    bigserial primary key,
  conta_id              bigint not null references public.contas(id) on delete cascade,
  pedido_id             bigint not null references public.obra_pedidos(id) on delete cascade,
  produto_id            bigint not null references public.catalogo_produtos(id),
  quantidade            numeric(12,3) not null check (quantidade > 0),
  quantidade_recebida   numeric(12,3),
  mov_ids               jsonb not null default '[]'::jsonb
);
create index if not exists idx_obra_pedido_itens on public.obra_pedido_itens (pedido_id);

-- rollback:
--   drop table if exists public.obra_pedido_itens;
--   drop table if exists public.obra_pedidos;
