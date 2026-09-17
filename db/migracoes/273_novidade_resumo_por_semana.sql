-- 273_novidade_resumo_por_semana.sql
-- A faixa de avisos passa a interromper UMA vez por semana.
--
-- A MEDIÇÃO QUE MOTIVOU (17/09/2026), pedida pelo dono: "vamos aos avisos, como
-- ficou?". A resposta honesta era ruim:
--
--   MANOEL (dono) ........ 47 por ler, ZERO lidos
--   JACQUELINE (vendedora) 28 por ler, ZERO lidos
--   THIAGO ............... 18 por ler, 10 lidos
--   PEDRO YAN ............ 15 por ler, 13 lidos
--   30 avisos publicados em 7 dias
--
-- A faixa dizia "1 de 42". Ninguém toca 42 vezes.
--
-- E O PRAZO DE 14 DIAS, criado dois dias antes pra segurar exatamente isto, não
-- ajudou: 42 dos 47 eram dos últimos 14 dias. Ele foi desenhado contra aviso
-- VELHO, e o problema era aviso DEMAIS. Foi a regra 5 do CLAUDE.md funcionando bem
-- demais — todo PR que muda tela escreve o aviso dele, e cinco entregas por dia
-- viram cinco interrupções por dia.
--
-- A ESCOLHA DO DONO, entre três: "um resumo por semana".
--
-- O QUE MUDOU NA TELA (/cockpit, a faixa em cima da Fila)
-- 1) A faixa mostra a SEMANA: "✨ 5 novidades esta semana · Ver →". O "Ver" abre a
--    lista da semana, com título e resumo de cada uma.
-- 2) Uma novidade sozinha continua aparecendo pelo TÍTULO. Agrupar serve pra domar
--    muitas, não pra esconder uma — "1 novidade esta semana" custaria um toque a
--    mais pra saber o que ela já podia ler dali.
-- 3) O contador conta SEMANAS ("1 de 2"), que é o número de vezes que a pessoa
--    ainda vai ser interrompida. "1 de 42" era a conta que ninguém tocou.
-- 4) O ✕ dispensa a faixa DAQUELA SEMANA — e NÃO marca nada como lido.
--
-- O ✕ MERECE O PARÁGRAFO. Em 16/09 o dono barrou a ideia de o ✕ dispensar tudo,
-- com argumento melhor que o meu: marcar como lido o que ninguém leu DESTRÓI
-- informação, porque "não lido" é o que a bolinha do Perfil conta e não existe
-- desmarcar. Com a faixa semanal isso seria pior — um toque apagaria cinco avisos
-- de uma vez. Então o ✕ grava numa tabela própria (migração 272) só o "não me
-- interrompa de novo por esta semana", e a bolinha continua dizendo a verdade.
--
-- E A LISTA DA SEMANA TAMBÉM NÃO MARCA NADA. Ver cinco títulos não é ter lido as
-- cinco coisas; marcar ali encheria a base de leituras que não aconteceram. Quem
-- marca é o aviso aberto, como sempre foi.
--
-- O QUE NÃO MUDOU: cada aviso continua existindo inteiro, na lista do Perfil, em
-- Novidades e no site (`GET /novidades.json`). O agrupamento é de LEITURA — juntar
-- no banco tiraria de cada entrega o registro dela.
--
-- O PORTÃO: `todos`. É a faixa de avisos, que toda conta tem, em qualquer nicho.
--
-- PRA QUEM: vendedor (a faixa é da Fila dele), dono e gestor (a mudança é do
-- sistema de avisos que eles também recebem).
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('aviso-resumo-por-semana', 'mudanca', 'todos', '{dono,gestor,vendedor}',
 'Os avisos agora chegam num resumo por semana',
 'A faixa de novidades no topo da Fila passou a interromper uma vez por semana, com tudo o que a semana trouxe, em vez de um aviso por vez.',
 '/cockpit',
 $txt$Estavam chegando avisos demais. Em sete dias saíram 30, e a faixa no topo da Fila mostrava um de cada vez: "1 de 42". Ninguém toca 42 vezes — e a medição mostrou isso: a maioria das pessoas nunca abriu nenhum.

AGORA A FAIXA FALA DA SEMANA: "5 novidades esta semana". Um toque em "Ver" abre a lista, com o título e o resumo de cada uma, e dali você entra na que interessar.

QUANDO A SEMANA TIVER UMA SÓ, ela continua aparecendo pelo nome — não faz sentido esconder um aviso atrás de "1 novidade".

O ✕ CONTINUA ALI e ficou mais útil: ele dispensa a faixa daquela semana inteira. E não marca nada como lido — a bolinha do seu Perfil continua mostrando o que você ainda não leu, e nada se perde. Se quiser ler depois, está tudo em Novidades.

Nada foi apagado nem juntado: cada novidade continua inteira na lista e no site de atualizações. O que mudou é só quantas vezes a gente bate na sua porta.$txt$,
 timestamptz '2026-09-17 16:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'aviso-resumo-por-semana';
