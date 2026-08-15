-- 149_cliente_cidade_uf.sql
-- Cidade e estado do cliente na base do lojista.
--
-- O orçamento de evento (147) já guarda cidade/UF do cliente e agora espelha o
-- cliente na aba Clientes ao salvar. Só que a tela de Clientes não tinha esses
-- campos: um "Teresina/OI" digitado errado no orçamento não tinha onde ser
-- corrigido. Ficam em `clientes` (a relação loja↔pessoa), junto de aniversário
-- e obs — é o cadastro que O LOJISTA mantém sobre o cliente dele; a identidade
-- (pessoas: cpf/celular/nome) segue intocada.
--
-- Aditivo e idempotente.

alter table public.clientes add column if not exists cidade text;
alter table public.clientes add column if not exists uf     varchar(2);

-- rollback:
--   alter table public.clientes drop column if exists cidade, drop column if exists uf;
