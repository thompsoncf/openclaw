-- 067_titulos_cliente.sql
-- Liga o titulo (a receber) ao CLIENTE, pra o fiado da venda de balcao aparecer na
-- ficha do cliente (em aberto) e amarrar na base de ouro. Idempotente, nao-destrutivo.

alter table titulos add column if not exists cliente_id bigint references clientes(id);
create index if not exists ix_titulos_cliente on titulos (cliente_id) where cliente_id is not null;

-- rollback (manual):
--   alter table titulos drop column if exists cliente_id;
