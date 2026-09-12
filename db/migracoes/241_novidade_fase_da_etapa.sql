-- 241_novidade_fase_da_etapa.sql
-- O aviso do seletor de fase na Régua (CLAUDE.md §5).
--
-- PÚBLICO 'todos' (§6): a fase é mecânica de funil, de qualquer ramo. O texto fala
-- de "o que já está vendido" e não de festa — quem vende mensalidade tem a mesma
-- pergunta com outras palavras, e o exemplo do corpo é neutro de propósito.
--
-- PRA QUEM: dono e gestor. É configuração da empresa, e mexe no número que eles
-- olham no fim do mês. O vendedor não abre a Régua.
--
-- POR QUE ESTE AVISO IMPORTA MAIS QUE OS OUTROS: mudar a fase muda o passado. Uma
-- etapa que vira pós-venda faz os leads que já estão nela entrarem nos ganhos do mês
-- retroativamente. É a diferença entre "arrumei o relatório" e "o relatório mudou
-- sozinho e ninguém sabe por quê" — por isso o corpo diz isso com todas as letras.
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('fase-da-etapa-na-regua', 'novidade', 'todos', '{dono,gestor}',
 'Dá para dizer, depois de criada, que uma etapa já é pós-venda',
 'Cada etapa do funil agora tem um seletor de fase na Régua: "ainda é venda" ou "já vendido". É a fase que decide o que entra nos ganhos do mês.',
 '/painel/prospeccao/regua',
 $txt$Toda etapa do funil responde a uma pergunta que não aparecia em lugar nenhum: o que está parado aqui já foi vendido, ou ainda está sendo conquistado?

É essa resposta — a fase da etapa — que decide o que entra nos ganhos do mês, no Cockpit e no painel do dono. Até agora ela só podia ser escolhida no momento de criar a etapa. Se você errasse, ou se a etapa mudasse de sentido com o tempo, não havia como corrigir.

Agora, na Régua, cada etapa que não é fixa tem um seletor ao lado do nome: "ainda é venda" ou "já vendido · pós-venda".

Duas coisas para saber antes de mexer.

A primeira: mudar a fase mexe no passado. Os clientes que já estão naquela etapa passam a contar como vendidos a partir do momento em que você salva, inclusive nos meses que já fecharam. Se o número do mês passado mudar depois que você mexer aqui, foi isto — e provavelmente era o número certo que estava faltando.

A segunda: a coluna muda de lugar no quadro. Etapa de pós-venda fica depois de Perdido, no fim da fila, e etapa de venda volta para o meio. Isso não é enfeite: é a ordem que o sistema usa para saber o que está "à frente" quando move um card sozinho, e uma etapa de pós-venda no meio da venda seria alcançada por engano. A tela recarrega sozinha para você ver onde a coluna foi parar.

As três etapas fixas não têm o seletor. "Novo" é a porta de entrada, e "Ganho" e "Perdido" são o resultado — o relatório inteiro depende de elas estarem onde estão.$txt$,
 timestamptz '2026-09-12 12:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'fase-da-etapa-na-regua';
