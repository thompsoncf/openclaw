-- 461_clientes_vendedor.sql
-- O CLIENTE CADASTRADO ANTES DA RESERVA (pedido do dono, 01/10/2026 — app de
-- estandes do Outlet Chic): a vendedora cadastra o lojista em "+ Novo cliente" e
-- manda o link de vendas pra ele; quando ele reserva por esse link, a venda já
-- nasce com o cadastro completo. `vendedor_id` = quem cadastrou: o cliente fica
-- na carteira dela (aba Clientes do app) até reservar. NULL = os clientes de
-- sempre, de todas as outras contas.
--
-- Dentro de DO e com a checagem das tabelas: a migração também roda em bancos
-- mínimos (teste de blindagem) sem `clientes` ou `membros`.
do $$
begin
    if to_regclass('public.clientes') is not null and to_regclass('public.membros') is not null then
        execute $q$
            alter table public.clientes add column if not exists vendedor_id bigint
                references public.membros(id) on delete set null
        $q$;
        execute $q$
            create index if not exists idx_clientes_vendedor
                on public.clientes (dono_id, vendedor_id) where vendedor_id is not null
        $q$;
    end if;
end $$;

-- rollback:
--   drop index if exists idx_clientes_vendedor;
--   alter table public.clientes drop column if exists vendedor_id;
