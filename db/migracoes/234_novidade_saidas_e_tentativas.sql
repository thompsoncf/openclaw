-- 234_novidade_saidas_e_tentativas.sql
-- O aviso das saídas da etapa e das tentativas como tarefas (CLAUDE.md §5).
--
-- PÚBLICO 'todos' (§6): as duas coisas são mecânica de funil, de qualquer nicho —
-- o que veio de eventos foi o pedido, não a regra. O texto não fala de festa, de
-- visita nem de data.
--
-- PRA QUEM: dono, gestor e vendedor. O vendedor entra porque é ele que sente as
-- duas na rotina: é o card dele que deixa de aceitar qualquer coluna, e é a fila
-- dele que passa a mostrar as tentativas pendentes.
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('saidas-e-tentativas-da-etapa', 'novidade', 'todos', '{dono,gestor,vendedor}',
 'Cada etapa pode dizer para onde o lead vai — e as tentativas de follow-up nascem com data',
 'Na Régua, cada coluna passa a poder declarar suas saídas e suas tentativas. O lead só vai para onde a etapa permite, e as tentativas aparecem como tarefas com prazo, sem ninguém precisar criar.',
 '/painel/prospeccao/regua',
 $txt$Duas coisas novas na Régua, e as duas nascem desligadas.

A primeira: cada etapa pode declarar para onde o lead pode ir. Marcando "Follow-up" e "Proposta" no Contactado, por exemplo, o card deixa de aceitar qualquer outra coluna — no quadro do painel e no aplicativo do vendedor. Se alguém tentar, a mensagem diz para onde pode ir, não só que não pode. Nenhuma caixa marcada é o que todas as contas têm hoje: o lead vai para onde quiser.

Isso vale para a mão, não para os gatilhos. Quando um orçamento é enviado ou um contrato é assinado, o card anda mesmo assim: o gatilho não escolhe para onde levar o lead, ele anota um fato que já aconteceu. Barrar um fato faria o funil mentir, que é o que essa régua existe para evitar.

A segunda: cada etapa pode declarar suas tentativas, em dias contados da entrada. Preenchendo "1,3,7" no Follow-up, todo lead que chega ali já nasce com três tarefas — D1, D3 e D7 — cada uma com data. A fila mostra quais já foram feitas, quais estão pendentes e quais passaram do prazo, e a tentativa conta pela conversa: mensagem enviada pelo celular do vendedor vale igual.

Quando as três acabam sem resposta, o sistema não encerra o lead sozinho. Ele fica marcado esperando a decisão de quem falou com o cliente — encerrar ou reativar —, porque encerrar exige motivo e o sistema não tem como saber qual é.

Deixando o campo em branco, tudo continua exatamente como está hoje.$txt$,
 timestamptz '2026-09-11 21:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'saidas-e-tentativas-da-etapa';
