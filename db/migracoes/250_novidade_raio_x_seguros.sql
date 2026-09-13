-- 250_novidade_raio_x_seguros.sql
-- O aviso do perfil de Raio-X da corretora. Seção 5 do CLAUDE.md: PR que muda
-- tela leva o aviso no mesmo PR. O portão `seguros` já existe desde a 243 —
-- este aviso é o segundo a usá-lo, que é o sinal de que criar o portão valeu.
--
-- POR QUE A MUDANÇA EXISTE, medido em 13/09/2026
-- (docs/mockups/raio_x_seguros_medido.html): caindo no perfil 'recorrente', a
-- corretora via "Mensalidade proposta × fechada" somando `mensal_centavos`.
-- Comissão de apólice vai em `setup_centavos`, então o bloco lia R$ 0 e escrevia
-- "o gargalo é depois da proposta" — num mês de vinte apólices fechadas. Campo
-- vazio a pessoa ignora; diagnóstico errado manda o dono cobrar a equipe pelo
-- motivo que não existe.
--
-- QUEM RECEBE, conferido na produção em 13/09/2026:
--   conta 37 · Liberal Neto / Liberal Seguros · Teresina-PI · trial · 1 membro
--              (única corretora da base; `contas.nicho_id` ainda nulo, então na
--              prática ela só verá isto depois de o nicho ser marcado na tela)
--
-- PRA QUEM: dono, gestor E VENDEDOR. Diferente da 243, que era sobre configurar
-- a empresa: o funil, os nomes das colunas e a lista de por-que-perdeu são a
-- rotina do corretor, e "Cotação enviada" é a coluna que ele arrasta todo dia.
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('raio-x-seguros', 'novidade', 'seguros', '{dono,gestor,vendedor}',
 'O Raio-X e o funil agora falam de cotação, ramo e comissão',
 'Corretora de seguros ganhou medição própria: comissão no lugar de mensalidade, cotação no lugar de reunião, e os motivos de perda que só existem em seguro.',
 '/painel/raio-x',
 $txt$O Raio-X media sua corretora com a régua de quem vende mensalidade. Isso mudou.

O QUE ESTAVA ERRADO, e era pior que uma tela feia: o bloco principal somava mensalidade. Comissão de apólice não é mensalidade, então ele mostrava R$ 0 e concluía "o gargalo é depois da proposta" — mesmo num mês em que você fechou tudo. Um número vazio a gente ignora; uma frase dizendo onde está o gargalo faz cobrar a equipe pelo motivo errado.

AGORA O BLOCO É "Comissão proposta × fechada": o que foi cotado no mês, o que virou apólice, e a conta entre os dois. Sem "/mês", porque apólice não é mensalidade.

O FUNIL FALA A SUA LÍNGUA. A coluna que era "Reunião marcada" agora é "Cotação enviada", e o Raio-X conta "cotações que aconteceram" no lugar de reuniões. "Serviço mais proposto" virou "Ramo mais proposto" — auto, vida, residencial, frota.

POR QUE PERDEU ganhou o que só existe em seguro: "Renovou direto com a seguradora" (o cliente que pula o corretor não é o mesmo que foi pro concorrente), "Cobertura não atendeu" (que é diferente de preço), "Não tem perfil / recusado pela seguradora" e "Preço do prêmio". Essa lista é sua a partir da primeira abertura da tela — dá pra renomear, reordenar e acrescentar sem falar com ninguém.

SEGMENTO E PORTE CONTINUAM. Eles vêm do CNPJ, e num primeiro momento a gente ia tirar por causa do seguro auto, que é pessoa física. Ficaram porque você atende empresa também: valem pra essa metade da carteira.

O QUE AINDA NÃO EXISTE: a carteira de apólices, com vigência e renovação. É ela que vai ligar o aviso de "vence em 30 dias" — o mesmo mecanismo que hoje avisa uma casa de festas 30 dias antes do evento. O lugar já está reservado; falta guardar a vigência.$txt$,
 timestamptz '2026-09-13 18:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'raio-x-seguros';
