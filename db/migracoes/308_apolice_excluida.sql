-- 308_apolice_excluida.sql
-- DÁ PRA TIRAR UMA APÓLICE DA CARTEIRA SEM PERDER O REGISTRO DELA.
--
-- O PEDIDO (dono, 22/09/2026): "la no final coloca uma seta com o botão igual tem
-- na aba serviços no funil pra aparecer os comandos editar, excluir e demais
-- funções".
--
-- POR QUE NÃO É `DELETE`. Regra 0 — informação de cliente não se perde. Uma
-- apólice cadastrada errado precisa sair da carteira; o registro de que ela
-- existiu, do PDF que a originou e de quem a criou, não precisa sumir junto. E
-- desfazer um `delete` é restaurar backup; desfazer isto é um update.
--
-- NÃO SE CONFUNDE COM `perdida`. "Perdida" é desfecho de NEGÓCIO — o cliente
-- renovou com outra corretora, e isso é justamente o que a tela de perda existe
-- pra medir (migração 287). "Excluída" é ERRO DE CADASTRO: a apólice não deveria
-- estar ali. Misturar as duas envenenaria o funil de perda com engano de
-- digitação.
--
-- Aditivo e idempotente.

alter table public.apolices
    add column if not exists excluida_em  timestamptz,
    add column if not exists excluida_por bigint;

-- a carteira e a fila leem por conta; a exclusão entra no mesmo recorte
create index if not exists ix_apolices_vivas
    on public.apolices (conta_id, vigencia_fim)
 where excluida_em is null;

-- rollback:
--   drop index if exists public.ix_apolices_vivas;
--   alter table public.apolices drop column if exists excluida_em;
--   alter table public.apolices drop column if exists excluida_por;
