-- 233_funil_toques_da_etapa.sql
-- As tentativas de follow-up nascendo como tarefas, contadas da ENTRADA na etapa.
-- Regra 4 do FLUXO_FINAL_FUNIL_PRIME_EVENTOS_DESENVOLVEDOR_V3.
--
-- O QUE O DONO PEDIU
--   "No FOLLOW-UP, o período total será de 7 dias. As três tentativas devem nascer
--    automaticamente como tarefas em D1, D3 e D7, com prazo e alerta de atraso, sem
--    depender da lembrança do vendedor."
--
-- POR QUE ISSO NÃO É A ESCADA QUE JÁ EXISTIA
-- O follow-up (migração 218) já sobe uma escada de toques — mas relativa à ÚLTIMA
-- CONVERSA: cada toque reinicia a contagem a partir da mensagem que acabou de sair.
-- O pedido aqui é outro relógio: D1, D3 e D7 contados da entrada na etapa, com
-- total fechado em 7 dias. Os dois são defensáveis, e é por isso que nenhum dos
-- dois vira "o jeito certo" no código:
--
--   `toques_dias` VAZIO  → escada relativa à conversa (o que roda hoje, e o que
--                          continua valendo pra quem não configurar nada)
--   `toques_dias` = 1,3,7 → as três tarefas, ancoradas na entrada na etapa
--
-- Assim a etapa "Follow-up" da Prime declara 1,3,7 e ganha exatamente o que o
-- documento descreve, sem mudar o comportamento de nenhuma outra conta nem de
-- nenhuma outra etapa.
--
-- POR QUE ANCORAR NA ENTRADA IMPORTA
-- Com a escada relativa, um vendedor que tenta no D2 empurra o próximo toque pra
-- D2+3, e o "período total de 7 dias" vira sete dias depois do último esforço —
-- que pode ser um mês depois da entrada. Ancorado, os sete dias são sete dias, e a
-- pergunta "este lead já esgotou o follow-up?" tem uma resposta só.
--
-- O QUE ESTA MIGRAÇÃO NÃO FAZ: mover o lead pra Perdido no fim das três. Perder
-- exige motivo (regra 5), e só quem falou com o cliente sabe qual — o lead fica
-- marcado esperando a decisão. É a mesma razão pela qual o teto (230) não move
-- ninguém sozinho quando as renovações acabam.
--
-- Aditiva e idempotente.

alter table public.funil_etapas
  -- dias separados por vírgula, contados da ENTRADA na etapa. NULL = usa a escada
  -- relativa à conversa (funil_regua.fu_toques_dias), que é o padrão de sempre.
  add column if not exists toques_dias text;

comment on column public.funil_etapas.toques_dias is
  'tentativas em D1,D3,D7 contadas da entrada na etapa; null = escada relativa à conversa';

-- rollback:
--   alter table public.funil_etapas drop column toques_dias;
