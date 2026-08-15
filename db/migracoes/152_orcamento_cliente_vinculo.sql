-- 152_orcamento_cliente_vinculo.sql
-- O orçamento passa a APONTAR pro cliente, em vez de só copiar o texto dele.
--
-- Até aqui nome/documento/endereço/telefone do cliente eram copiados pro
-- orçamento na hora de salvar e congelavam ali. Corrigir na aba Clientes não
-- mudava a folha — a folha nem sabia que aquele cliente existia. Com o vínculo,
-- orçamento AINDA NÃO ASSINADO relê do cadastro (corrigiu, reimprimiu, saiu
-- certo); orçamento assinado fica congelado, que é o que o cliente aprovou.
--
-- E pra ter onde corrigir: endereço e CEP entram na aba Clientes, junto de
-- cidade/uf (149). Ficam em `clientes` — o cadastro que O LOJISTA mantém sobre
-- o cliente dele; a identidade (pessoas: cpf/celular/nome) segue intocada.
--
-- Aditivo e idempotente.

alter table public.orcamentos add column if not exists cliente_id bigint;

-- sem FK de propósito: `clientes` é a relação loja↔pessoa e pode ser arquivada;
-- um orçamento antigo apontando pra relação arquivada continua válido (cai no
-- texto congelado). Índice só pra achar os orçamentos de um cliente.
create index if not exists idx_orcamentos_cliente
    on public.orcamentos (cliente_id) where cliente_id is not null;

alter table public.clientes add column if not exists endereco text;
alter table public.clientes add column if not exists cep      text;

-- rollback:
--   drop index if exists idx_orcamentos_cliente;
--   alter table public.orcamentos drop column if exists cliente_id;
--   alter table public.clientes drop column if exists endereco, drop column if exists cep;
