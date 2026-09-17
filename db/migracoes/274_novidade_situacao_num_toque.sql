-- 274_novidade_situacao_num_toque.sql
-- A situação do lead vira botão — e as etapas que sumiam voltam.
--
-- O QUE MUDOU NA TELA (a janela que abre no card do funil e no "Abrir ficha" do
-- Follow-up; e a tela do lead no app)
-- 1) O <select> de situação virou uma FILEIRA DE BOTÕES. Um toque, sem roleta.
-- 2) O passo seguinte nasce destacado, com a seta.
-- 3) Ganho e perdido saem da fileira e ganham uma linha própria, depois de um
--    traço, em verde e vermelho, com ✓ e ✕.
-- 4) Ao lado de "Situação" aparece "parado há N dias".
--
-- E O CONSERTO QUE IMPORTA MAIS QUE O DESENHO — foi o dono quem viu, olhando a
-- tela: "você diz ter 9, na Prime só aparecem 6, cadê o resto?".
--
-- A mesma janela, no MESMO lead, oferecia listas diferentes conforme a porta:
--
--   quadro do funil → clicar no card ........ 6 situações
--   Follow-up → Abrir ficha ................. 9
--   ficha completa .......................... 9
--   app → lead → Etapa no funil ............. 5 (+ ganho/perdido em botão)
--
-- A causa era uma variável fazendo dois trabalhos em web/painel_prospeccao.py: a
-- lista de etapas é filtrada por `sai_do_quadro` pra montar as COLUNAS do quadro
-- (migração 238, pedido do dono, e está certo) — e o seletor era montado, mais
-- abaixo, a partir dessa mesma variável já filtrada. "Não vira coluna" virou, sem
-- ninguém querer, "não dá pra escolher".
--
-- O QUE ISSO CUSTOU, conta 34: `ganho` se chama "Evento Realizado" e está marcada
-- sai_do_quadro. Quem trabalha PELO QUADRO — a tela onde o vendedor passa o dia —
-- não tinha como marcar a venda. Em dois meses, num funil de 398 leads, entraram
-- 7 em ganho e 15 em "Agendado Visita" (que também sumia). No app, "Agendado
-- Visita" — o passo da qualificação — não existia de jeito nenhum.
--
-- E OS NOMES, no app: a tela "Leads da equipe" montava os rótulos de uma tabela
-- fixa de quatro no código. O gestor da Prime lia "Qualificado" onde o painel diz
-- "Agendado Visita", "Proposta" onde diz "Negociação", e "Evento_Realizado" com
-- sublinhado no meio da palavra. Agora vem da conta, como manda a regra 6.
--
-- O PORTÃO: `servico`. Quem vende produto não tem funil (as telas de caixa e
-- pedido não passam por etapa), então `todos` daria aviso de tela que a pessoa
-- não tem.
--
-- PRA QUEM: dono, gestor e vendedor. É o vendedor quem mais ganha — é a rotina
-- dele que muda.
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('situacao-num-toque', 'novidade', 'servico', '{dono,gestor,vendedor}',
 'Mudar a situação do lead virou um toque — e as etapas que sumiam voltaram',
 'A lista de situações do lead virou uma fileira de botões, com o próximo passo em destaque, e as etapas que o quadro esconde voltaram a poder ser escolhidas.',
 '/painel/prospeccao',
 $txt$Abrindo um lead — no quadro do funil, no Follow-up ou no app — a situação dele agora é uma fileira de botões.

UM TOQUE, NÃO TRÊS

Era uma listinha suspensa no canto. No celular isso é um toque pra abrir a roleta, outro pra escolher e outro pra confirmar, com a lista tampando a tela. Agora é o botão, direto.

O PRÓXIMO PASSO JÁ VEM ACESO

A etapa logo depois da atual nasce destacada, com uma seta. É quase sempre o que você veio fazer ali.

GANHO E PERDIDO SAÍRAM DA FILA

Eles agora ficam numa linha separada embaixo, em verde e vermelho, com ✓ e ✕. Antes "Perdido" ficava encostado na etapa de fechamento numa lista única — e um toque torto marcava como perda um lead que tinha acabado de fechar.

"PARADO HÁ N DIAS"

Do lado da palavra Situação aparece há quanto tempo o lead está onde está. Sem abrir relatório nenhum.

O QUE ESTAVA FALTANDO, E VOLTOU

Se o seu funil tem etapa marcada pra não aparecer no quadro, ela sumia também do seletor — e aí não dava pra escolher. Dependendo da conta, isso incluía a própria etapa de venda fechada: dava pra ver o lead, mas não dava pra marcar que ele foi ganho, a não ser abrindo a ficha completa por outro caminho.

Agora elas aparecem, com a borda tracejada e o aviso de que o card sai do quadro ao entrar ali. O quadro continua limpo como você deixou; o que mudou é que a escolha existe.

NO APP

A mesma coisa: as etapas escondidas voltaram pros botões (tracejadas), e os nomes agora são os que você deu ao seu funil — se você chamou a etapa de "Agendado Visita", é isso que aparece, e não mais um nome genérico.$txt$,
 timestamptz '2026-09-17 18:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'situacao-num-toque';
