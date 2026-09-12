-- 248_novidade_temperatura_por_fato.sql
-- O aviso da temperatura sugerida pelos fatos (CLAUDE.md §5).
--
-- PÚBLICO 'todos' (§6): temperatura de lead é de qualquer ramo. O texto não nomeia
-- festa nem visita, e os limiares que ele cita são os do ramo de quem lê.
--
-- PRA QUEM: dono, gestor E VENDEDOR — como o aviso da fila (246). A temperatura
-- aparece no card e na fila dele, e ele é quem corrige quando a sugestão erra.
-- Ligar continua sendo de dono/gestor, e o corpo diz isso.
--
-- O CORPO PRECISA SER FRANCO sobre uma coisa: ligar isto reescreve a temperatura de
-- quase todos os leads de uma vez. Na Prime seriam 289 de 319. Um aviso que não diz
-- isso entrega uma tela que amanheceu diferente sem explicação.
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('temperatura-por-fato', 'novidade', 'todos', '{dono,gestor,vendedor}',
 'A temperatura do lead passa a sair da conversa, e não do carimbo de entrada',
 'Quente, morno e frio deixam de ser um carimbo fixo na entrada do funil: o sistema sugere a temperatura pelo que já aconteceu na conversa, e o vendedor corrige quando errar.',
 '/painel/prospeccao/regua',
 $txt$Até agora, todo lead que entrava no funil era marcado como QUENTE — e nada nunca o esfriava. Com o tempo, isso deixa quase todo mundo quente, e a informação para de informar: se todos são prioridade, ninguém é.

Agora o sistema pode sugerir a temperatura a partir do que já está na conversa, sem ninguém precisar preencher nada:

QUENTE — o cliente falou por último, e faz pouco tempo. Responder é o sinal mais honesto de interesse que existe: quem pede orçamento, informa a data ou pergunta disponibilidade está, antes de tudo, respondendo.

MORNO — a conversa existe dos dois lados, mas a última palavra é sua e ele ainda não voltou.

FRIO — você já tentou algumas vezes e ele não respondeu, ou ele sumiu há tempo demais, ou nunca falou nada.

Quantas horas contam como "faz pouco tempo", quantos dias são "tempo demais" e quantas tentativas esfriam: tudo isso é ajustável na Régua, e cada ramo começa com um número diferente — quem vende evento decide mais rápido do que quem vende mensalidade.

O vendedor continua mandando. A sugestão é um ponto de partida; mudar a temperatura na ficha continua funcionando como sempre, e a mudança à mão vale.

Três coisas antes de ligar.

Lead já fechado ou já perdido fica de fora. A temperatura é sobre quem ainda pode comprar.

Ligar reescreve de uma vez. Como quase todo lead está quente hoje, a primeira passada muda a temperatura de quase todos. Cada mudança fica registrada no histórico do lead, com o valor anterior.

Por isso existe o modo de ensaio. Em "em ensaio", o sistema calcula e mostra o que mudaria, sem gravar nada. É o jeito de ver o tamanho da mudança antes de aceitá-la — e é por onde recomendamos começar.$txt$,
 timestamptz '2026-09-12 23:30:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'temperatura-por-fato';
