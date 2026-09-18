-- 282_perdido_automatico.sql
-- O PERDIDO AUTOMÁTICO: o modo, os toques e o corte por data.
--
-- Pedido do dono em 18/09/2026, ao remontar o funil da Prime em sete colunas:
--   "perdido - nao respondeu durante os 7 dias ou nao tem mais interassao ou nao
--    respondeu o follow"
-- e, sobre o que é um follow-up: "sao 3 tocs e o 4 fica como perdido".
--
-- POR QUE TRÊS COLUNAS E NÃO UMA CHAVE LIGA/DESLIGA
-- Medido na conta 34 em 18/09, nos 334 leads de Contatado: 240 passaram do teto
-- de 7 dias com a bola conosco — mas 122 deles levaram UM ÚNICO toque e 51
-- levaram dois. Só 67 foram tocados três vezes ou mais. Fechar por calendário
-- puro carimbaria de "não respondeu" o lead que ninguém chamou. Por isso o
-- número de toques é config, e não constante: é ele que separa "a empresa
-- tentou" de "a empresa esqueceu".
--
-- `perdido_desde` existe porque o dono pediu um corte com estas palavras: "fazer
-- desse mês de setembro pra frente". Sem corte, ligar numa conta com fila velha
-- fecharia anos de histórico numa passada só.
--
-- `perdido_modo` NASCE 'off' em toda conta. Em 'observando' o motor conta o que
-- teria fechado e não fecha nada.
--
-- Aditiva e idempotente.

alter table public.funil_regua
  add column if not exists perdido_modo       text not null default 'off',
  add column if not exists perdido_toques_min int  not null default 3,
  add column if not exists perdido_desde      date;

alter table public.funil_regua
  drop constraint if exists funil_regua_perdido_modo_ck;
alter table public.funil_regua
  add constraint funil_regua_perdido_modo_ck
  check (perdido_modo in ('off', 'observando', 'ligado'));

-- toques negativos fechariam tudo sem tentativa nenhuma
alter table public.funil_regua
  drop constraint if exists funil_regua_perdido_toques_ck;
alter table public.funil_regua
  add constraint funil_regua_perdido_toques_ck
  check (perdido_toques_min >= 0 and perdido_toques_min <= 20);

-- rollback:
--   alter table public.funil_regua drop column if exists perdido_desde;
--   alter table public.funil_regua drop column if exists perdido_toques_min;
--   alter table public.funil_regua drop column if exists perdido_modo;
