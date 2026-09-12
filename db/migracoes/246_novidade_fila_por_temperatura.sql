-- 246_novidade_fila_por_temperatura.sql
-- O aviso da fila de prioridade por temperatura (CLAUDE.md §5).
--
-- PÚBLICO 'todos' (§6): temperatura de lead é de qualquer ramo — quem vende festa e
-- quem vende mensalidade têm igualmente clientes mais quentes que outros. O texto
-- não nomeia festa nem visita.
--
-- PRA QUEM: dono, gestor E VENDEDOR. Esta é a exceção que a §5 prevê — a fila é a
-- tela que o vendedor abre todo dia, e a ordem dela mudando é a rotina DELE que
-- muda. Ligar continua sendo de dono/gestor, e o corpo diz isso.
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('fila-por-temperatura', 'novidade', 'todos', '{dono,gestor,vendedor}',
 'A fila do Follow-up pode colocar os leads quentes na frente',
 'A temperatura do lead passa a poder ordenar a fila do vendedor, em seis níveis, começando por quem está quente e esperando resposta da equipe.',
 '/painel/follow-up',
 $txt$A temperatura do lead — quente, morno, frio — existe no sistema há muito tempo e aparece colorida no card do funil. Até agora ela não fazia nada: era informação para olhar, não para agir.

Na tela de Follow-up, a fila passa a poder ser ordenada por ela, em seis níveis, nesta ordem:

1º quente esperando resposta da equipe. 2º tarefa atrasada. 3º quente com a próxima ação vencendo. 4º respondeu e está esperando retorno. 5º morno com chance de avançar. 6º o resto, no fluxo normal.

Cada lead aparece em um nível só, o mais urgente dele. Um cliente quente, esperando a equipe e com tarefa atrasada é uma pessoa, e aparece uma vez, no topo.

Dentro de cada nível a ordem continua sendo a de sempre — quem está mais atrasado, quem tem a data mais próxima. Sem isso o primeiro nível viraria uma lista de quentes em ordem aleatória.

Duas coisas que não mudam.

A temperatura não muda a etapa do lead. Um cliente pode ficar em Contatado e estar quente: o que ela muda é a ordem de atendimento, não onde ele está no funil. Para mudar de etapa continua sendo preciso um avanço concreto — visita, proposta, sinal, contrato.

E a fila continua como está até alguém ligar. A ordem nova é uma escolha do dono ou do gestor, na Régua, em "Ordem da fila do vendedor". Enquanto ninguém mexer, a fila é a mesma de hoje.$txt$,
 timestamptz '2026-09-12 22:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'fila-por-temperatura';
