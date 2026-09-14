-- 255_novidade_funil_do_ramo.sql
-- O aviso do funil que passa a avisar quando não está no modelo do ramo.
-- Seção 5 do CLAUDE.md: PR que muda tela leva o aviso, no mesmo PR.
--
-- O PORTÃO É `todos`, e é a escolha certa aqui pela primeira vez em três avisos
-- seguidos: a faixa e o bloco de adoção valem pra QUALQUER ramo. Medido em
-- 14/09/2026, o funil fora do modelo alcança consultoria, eventos e seguros ao
-- mesmo tempo — mirar num nicho deixaria de fora justamente quem mais precisa.
--
-- QUEM RECEBE, conferido na produção em 14/09/2026 — as contas com funil, e o que
-- cada uma vai ver:
--   conta  3 · ZAQ (consultoria)      6 colunas genéricas · a faixa aparece
--   conta 23 · Rawilson (consultoria) 6 colunas genéricas · a faixa aparece
--   conta 30 · (consultoria)          6 colunas genéricas · a faixa aparece
--   conta 34 · PRIME (eventos)        8 colunas dela      · a faixa NÃO aparece,
--              porque `desencontro` não conta rótulo que o dono escreveu — é o
--              caso que a função existe pra respeitar
--   conta 35 · DOCE MELL (eventos)    6 genéricas · a faixa aparece (é o caso que
--              o `funil_modelo` cita pelo nome desde 11/09 e continuava parado)
--   conta 37 · LIBERAL (seguros)      "Reunião marcada" · a faixa aparece
--   conta 16 · SUPER FIT (suplementos) e conta 7 (sem nicho): sem diferença a
--              apontar hoje, então nada aparece — e é assim que tem que ser.
--
-- PRA QUEM: dono e gestor. Decisão do dono em 14/09: "dono e gestor". O vendedor
-- vê o quadro todo dia e não decide nome de coluna; faixa que ele não pode resolver
-- vira barulho diário até ele aprender a ignorar, que é como se estraga um aviso.
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('funil-do-ramo-avisa', 'novidade', 'todos', '{dono,gestor}',
 'O funil avisa quando não está no modelo do seu ramo',
 'O quadro passa a mostrar quando suas colunas não batem com as do seu ramo, e a lista de motivos de perda ganhou o mesmo botão de adotar que as colunas já tinham.',
 '/painel/prospeccao',
 $txt$Desde 11 de setembro cada ramo tem um modelo de funil — as colunas que uma empresa como a sua costuma usar. Só que o lugar de adotar esse modelo era um bloco dentro da Régua, e ninguém abre a Régua sem motivo. Resultado: de oito empresas com funil, uma tinha as colunas do próprio ramo.

AGORA O QUADRO AVISA. Quando suas colunas não batem com as do seu ramo, aparece uma faixa no topo com quantas são diferentes e um atalho pra ver o que mudaria. Um clique, você marca o que quiser, e pronto.

E ELA NÃO APARECE PRA QUEM RENOMEOU DE PROPÓSITO. Se você chamou uma coluna do jeito da sua empresa, isso é escolha sua: o sistema passou a guardar quem deu cada nome, e não conta as suas como diferença. Sem isso a faixa ficaria acesa pra sempre em quem mais cuidou do próprio funil.

ISSO TAMBÉM CONSERTA UMA GROSSERIA. Quando o modelo propunha trocar um nome que o PRÓPRIO SISTEMA tinha escolhido, ele dizia "você já renomeou esta etapa" e deixava a caixa desmarcada. Não era verdade, e fazia a correção parecer um capricho seu. Agora ele sabe a diferença: o que veio de semente vem marcado; o que é seu, não.

E A LISTA DE "POR QUE PERDEMOS" ganhou o mesmo botão. Antes só as colunas tinham. Quem mudou de ramo — ou abriu o funil antes de escolher o ramo — ficava com a lista errada e sem caminho de volta, tendo que digitar tudo de novo.

NADA MUDA SOZINHO. Nem no quadro, nem na lista: toda alteração continua sendo você marcando a caixa e clicando. E nada é apagado — coluna fora do modelo sai do quadro com os leads intactos, motivo fora do modelo sai da lista de escolha e continua aparecendo na ficha de quem já foi perdido por ele.$txt$,
 timestamptz '2026-09-14 19:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'funil-do-ramo-avisa';
