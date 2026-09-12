-- 244_novidade_fundir_etapas.sql
-- O aviso do "fundir em…" no editor de etapas (CLAUDE.md §5).
--
-- PÚBLICO 'todos' (§6): juntar duas colunas é mecânica de funil, de qualquer ramo.
-- O texto não nomeia festa nem visita — o exemplo é genérico de propósito.
--
-- PRA QUEM: dono e gestor. Quem edita a estrutura do funil. O vendedor vê a coluna
-- sumir, mas não é ele quem funde — e avisá-lo de um botão que ele não tem seria
-- avisar do que não muda a rotina dele.
--
-- POR QUE ESTE AVISO PRECISA SER FRANCO: fundir MOVE LEAD. É a primeira ação da
-- Régua que mexe em informação do cliente, e não em configuração. O corpo diz o que
-- acontece com cada lead, onde fica o registro, e que a etapa não é apagada — porque
-- "some a coluna" e "some o cliente" são a mesma frase pra quem está olhando a tela.
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('fundir-etapas-do-funil', 'novidade', 'todos', '{dono,gestor}',
 'Agora dá para juntar duas colunas do funil sem perder nada',
 'No editor de etapas, escolha uma coluna em "fundir em…" e os leads passam para ela, cada um com registro no histórico. A coluna vazia sai do quadro sem ser apagada.',
 '/painel/prospeccao',
 $txt$Até agora, remover uma coluna do funil só era possível quando ela estava vazia — e a própria tela dizia "mova os leads primeiro", sem oferecer um jeito de mover. Com trinta leads na coluna, isso queria dizer arrastar card por card.

No editor de etapas (⚙️ Editar etapas do funil, dentro do Funil) cada coluna ganhou um seletor "fundir em…" e um botão ⇥. Você escolhe para onde os leads vão, confirma, e eles passam de uma vez.

O que acontece com cada lead:

Ele muda de coluna e ganha uma linha no histórico dizendo de onde veio, para onde foi, e quem apertou o botão. Nada de nome, conversa, orçamento, contrato ou data muda — só a coluna.

O que acontece com a coluna de origem:

Ela fica vazia e sai do quadro, mas não é apagada. Isso é de propósito: o histórico dos leads que passaram por ela aponta para o nome dela, e apagá-la deixaria o passado sem referência. Se depois você quiser removê-la de vez, o ✕ agora funciona — a coluna está vazia.

Antes de confirmar, o aviso diz quantos leads vão andar e para onde. Se o número não bater com o que você esperava, é sinal de parar e conferir: fundir é fácil de fazer e trabalhoso de desfazer à mão.

Etapas fixas (a de entrada e as de resultado) não fundem — o relatório inteiro depende delas.$txt$,
 timestamptz '2026-09-12 20:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'fundir-etapas-do-funil';
