-- 232_funil_saidas_da_etapa.sql
-- Para onde um lead pode ir a partir de cada etapa. Regra 3 do
-- FLUXO_FINAL_FUNIL_PRIME_EVENTOS_DESENVOLVEDOR_V3.
--
-- O QUE O DONO PEDIU
--   "Ao sair de CONTACTADO, existem somente dois caminhos: NEGOCIAÇÃO ou
--    FOLLOW-UP. O sistema não deve permitir outra saída do CONTACTADO."
--
-- POR QUE ISSO É UMA COLUNA, E NÃO UM `if` COM AS DUAS CHAVES DENTRO
-- Mesma razão do teto (migração 230) e da herança (228): a regra é da Prime, a
-- mecânica é de todo mundo. Aqui cada etapa declara as suas saídas; o Contactado
-- da Prime declara duas, e outra empresa declara outras — ou nenhuma, que é como
-- toda conta nasce e como o funil sempre funcionou.
--
-- NULL = SEM RESTRIÇÃO, e é o padrão. Não existe um interruptor separado pra isto:
-- a coluna vazia já é o desligado, e um segundo botão pra dizer a mesma coisa só
-- daria duas fontes de verdade pra mesma pergunta.
--
-- O QUE A TRAVA NÃO ALCANÇA, DE PROPÓSITO: o gatilho.
-- Gatilho não é alguém escolhendo para onde levar o card — é um FATO que já
-- aconteceu sendo anotado (orçamento enviado, sinal pago, contrato assinado).
-- Barrar um fato faria o funil voltar a mentir, que é exatamente o problema que
-- esta régua inteira existe pra resolver: em 18/08/2026, 74 dos 81 leads "parados
-- em Novo" já tinham resposta nossa na conversa. A trava é sobre a MÃO.
--
-- Aditiva e idempotente.

alter table public.funil_etapas
  -- chaves separadas por vírgula. NULL ou vazio = pode ir pra qualquer etapa.
  add column if not exists saidas_permitidas text;

comment on column public.funil_etapas.saidas_permitidas is
  'chaves de etapa separadas por vírgula para onde a MÃO pode levar o lead daqui; '
  'null = sem restrição. O gatilho não é barrado: fato é fato.';

-- rollback:
--   alter table public.funil_etapas drop column saidas_permitidas;
