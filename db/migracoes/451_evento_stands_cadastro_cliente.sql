-- 451_evento_stands_cadastro_cliente.sql
-- O cliente do stand vira CADASTRO de verdade (aprovado na maquete, 30/09/2026).
--
-- Até aqui quem reservava um stand pela página virava só uma linha de
-- `prospeccao` (nome + WhatsApp) — sem CPF/CNPJ, sem endereço, sem ninguém pra
-- assinar. O contrato saía com "—" nos dados do contratante. Agora:
--
--  1) o stand aponta pro cliente (`evento_stands.cliente_id`), e quem
--     reserva já nasce em `clientes` (a aba Clientes que o dono já usa), sem
--     duplicar: o Zaq junta por CNPJ/CPF ou WhatsApp;
--  2) `clientes` ganha os DOIS campos que o contrato pede e o cadastro ainda
--     não tinha: razão social (como o contratante sai no documento) e o
--     representante legal (quem assina pelo lojista). O nome que o cliente
--     digita na página passa a ser o NOME FANTASIA (`clientes.nome`).
--
-- Sem FK em evento_stands.cliente_id de propósito: `clientes` é a relação
-- loja↔pessoa e pode ser arquivada (ativo=false); o stand vendido não pode
-- ficar preso a isso. Mesmo desenho de orcamentos.cliente_id (152).
--
-- Aditivo e idempotente.

alter table public.clientes add column if not exists razao_social text;
alter table public.clientes add column if not exists representante text;

alter table public.evento_stands add column if not exists cliente_id bigint;
create index if not exists idx_evento_stands_cliente
    on public.evento_stands (cliente_id) where cliente_id is not null;

-- rollback:
--   drop index if exists idx_evento_stands_cliente;
--   alter table public.evento_stands drop column if exists cliente_id;
--   alter table public.clientes drop column if exists razao_social, drop column if exists representante;
