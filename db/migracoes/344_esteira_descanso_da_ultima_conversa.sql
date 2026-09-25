-- 344_esteira_descanso_da_ultima_conversa.sql
-- Limpeza única: tirar da esteira quem já tinha sido tratado antes de entrar.
--
-- POR QUE ESTA MIGRAÇÃO EXISTE
-- Até hoje `entrar()` escolhia quem cobrar só pelo tempo parado NA ETAPA. Mandar
-- mensagem não move etapa — então o vendedor que fazia follow-up sem mover o card
-- era cobrado pelo mesmo lead todo dia, e o placar dele marcava zero, porque
-- `resolver()` só enxerga ação DEPOIS de `entrou_em`.
--
-- Medido na Prime (conta 34) em 25/09/2026. Na quarta 23/09 a JACQUELINE mandou
-- 233 mensagens para 64 leads. Na quinta e na sexta a esteira cobrou 10 por dia, e
-- nos dois dias os 10 eram leads que ela já tinha tratado na quarta:
--
--     dia      entraram   já falados nos 3 dias antes   resolvidos
--     21/09       10                  0                      8
--     22/09       10                  0                      9
--     23/09       10                 10                      0
--     24/09       10                 10                      0
--
-- O portão do descanso (em `finance/esteira.py:entrar`) impede que isso se repita.
-- Esta migração resolve o que já está aberto: as linhas que, sob a regra nova,
-- nunca teriam entrado.
--
-- POR QUE `resolvido_em` VAI NA DATA DA MENSAGEM, E NÃO EM `now()`
-- `resumo()` conta `resolvido_em >= desde`, e a janela do aviso da manhã é a
-- última cobrança. Carimbar com `now()` faria 35 leads "tratados" aparecerem de
-- uma vez no aviso de amanhã — um pico que ninguém trabalhou, mentindo para o
-- outro lado para compensar a mentira de hoje. Com a data real da mensagem que de
-- fato tratou o lead, o histórico de cada dia fica certo e nenhum placar inventa.
--
-- A RESOLUÇÃO É 'falou', e é a verdade: houve mensagem nossa para o lead. Não é
-- 'moveu' (o card não mudou) nem 'fechou' (ninguém ganhou ou perdeu).
--
-- VALE PARA TODAS AS CONTAS, não só a 34: o defeito é do código, não da Prime.
-- Efeito medido na conta 34 antes de aplicar — Jacqueline 36 → 13, Pedro 50 → 39,
-- Thiago 52 → 52 (nada a limpar: a carteira dele é abandonada de verdade, que é
-- exatamente o que a esteira existe para mostrar), zaq teste 2 → 1.

update follow_up_esteira e
   set resolvido_em = u.ultima_out,
       resolucao    = 'falou'
  from (select e2.id,
               (select max(m.criado_em)
                  from conversas cv join mensagens m on m.conversa_id = cv.id
                 where cv.prospeccao_id = e2.prospeccao_id and m.direcao = 'out') as ultima_out,
               fe.teto_dias
          from follow_up_esteira e2
          join funil_etapas fe
            on fe.conta_id = e2.conta_id and fe.chave = e2.etapa
         where e2.resolvido_em is null and e2.fechado_em is null
           and fe.teto_dias is not null and fe.teto_dias > 0) u
 where e.id = u.id
   and u.ultima_out is not null
   -- exatamente o portão novo, lido ao contrário: falamos com este lead dentro do
   -- descanso da etapa, logo ele não deveria ter entrado.
   and u.ultima_out >= e.entrou_em - make_interval(days => u.teto_dias);
