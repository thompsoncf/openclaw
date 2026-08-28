-- 189_contrato_enviado_em.sql
-- O contrato tinha "quando nasceu" (`criado_em`) e "quando foi assinado"
-- (`assinado_em`), mas não "quando foi MANDADO pra assinar" — e é esse o instante
-- que separa duas realidades diferentes na tela do funil: um contrato pronto que
-- ninguém ainda mandou pro cliente, e um contrato que já está na mão do cliente
-- esperando a assinatura voltar. Sem essa marca, os dois pareciam a mesma coisa
-- (nenhum selo, nenhum aviso), e um contrato de verdade mandado sumia da vista.
--
-- Aditivo e idempotente.
alter table public.contratos add column if not exists enviado_em timestamptz;

-- Os contratos que já existem nasceram todos com `status='enviado'` desde a
-- criação — a coluna nunca separou "documento pronto" de "documento na mão do
-- cliente". Sem backfill, todo contrato já mandado (inclusive um mandado hoje,
-- antes desta coluna existir) apareceria como "nunca mandado" assim que a tela
-- nova subir — a regressão exata que esta coluna existe pra evitar. Aproximação:
-- `criado_em`, que é o melhor dado que já existe pra "desde quando está esperando
-- assinatura".
update public.contratos
   set enviado_em = criado_em
 where enviado_em is null;

-- rollback:
--   alter table public.contratos drop column if exists enviado_em;
