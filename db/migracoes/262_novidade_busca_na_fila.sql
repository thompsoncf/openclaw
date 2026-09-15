-- 262_novidade_busca_na_fila.sql
-- A Fila do app ganha busca e dois recortes: "com proposta" e "com data".
--
-- O QUE MUDOU NA TELA (/cockpit)
-- 1) Uma caixa de busca no topo da Fila. Procura por NOME (pedaço do nome, sem
--    precisar de acento) e por NÚMERO (casa pelos 8 últimos dígitos, que é como o
--    mesmo contato aparece gravado de vários jeitos).
-- 2) Duas pílulas novas, no mesmo seletor dos meses: "📄 com proposta" e
--    "📅 com data".
-- 3) O card passa a mostrar a proposta do lead: "📄 nº 23 · aprovada".
--
-- A CONTA QUE MOTIVOU, conta 34, 15/09/2026, leads ABERTOS por vendedor:
--     Pedro Yan    146 abertos · 62 com data · 10 com proposta · 105 há 15+ dias
--     Jacqueline   114 abertos · 39 com data ·  3 com proposta ·  71 há 15+ dias
--     Thiago        87 abertos · 37 com data ·  4 com proposta ·  47 há 15+ dias
-- O cliente liga e pergunta do orçamento dele. Com 146 cartões abertos, o
-- vendedor rolava a lista procurando o nome no meio da ligação — e a Fila abre no
-- MÊS CORRENTE, então quem entrou em agosto nem estava na tela.
--
-- A BUSCA É NO SERVIDOR, e essa é a decisão que importa. Filtrar o que já está
-- renderizado (o jeito que os Relatórios usam) responderia "não achei" pra um
-- lead de agosto que existe, porque a consulta também corta em 100 leads. Por
-- isso a busca atravessa o mês E o corte, e diz em cima do resultado ONDE
-- procurou: "Busca em todos os abertos". Um "não achei" que não diz onde
-- procurou é pior do que não buscar.
--
-- OS NÚMEROS DAS DUAS PÍLULAS vêm da carteira INTEIRA (consulta própria, sem o
-- corte de 100), senão o Pedro veria "com proposta 8" onde são 10 — justamente
-- quem mais precisa do filtro veria o número errado.
--
-- O PORTÃO: `servico`. Busca e "com proposta" valem em qualquer nicho que tenha
-- funil. Dentro da tela, "com data" segue `vendas.vende_data` (§6) — o MESMO
-- portão que já decide se a Fila mostra o grupo "📅 sem data". Quem vende
-- mensalidade não tem festa, e a pílula seria uma lista vazia.
--
-- PRA QUEM: vendedor. É a tela dele, e o gestor que também vende cai na mesma.
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('busca-na-fila-do-app', 'novidade', 'servico', '{vendedor}',
 'Agora dá pra procurar um lead na Fila',
 'A Fila do aplicativo ganhou busca por nome e por número, e dois atalhos novos: os leads que já têm proposta e os que já têm data marcada.',
 '/cockpit',
 $txt$O cliente liga perguntando do orçamento dele, e você tinha que rolar a lista inteira procurando o nome — no meio da ligação. Quem tem mais de cem leads abertos sabe do que estou falando.

AGORA TEM BUSCA, no topo da Fila.

Digite um pedaço do nome ("caro" acha "Maria Carolina") ou o número do telefone. Não precisa de acento, não precisa do DDD inteiro: os últimos dígitos bastam.

E ela procura em TODOS os seus leads abertos, não só no mês que está na tela. Isso é de propósito: a Fila abre no mês corrente, então um lead de agosto não estaria à vista — e é exatamente ele que o cliente costuma estar cobrando. Em cima do resultado a tela diz onde procurou, pra você nunca ficar na dúvida se "não achei" quer dizer "não existe".

Se não achar, também diz: procurou por nome e por número, em todos os meses. Se o lead for de um colega, ou já estiver ganho ou perdido, ele não está nesta fila.

DOIS ATALHOS NOVOS, junto das pílulas de mês:

"📄 com proposta" — só os leads que já têm um orçamento feito. É a fila mais curta e a mais perto do dinheiro.

"📅 com data" — só os que já têm data marcada. (Este só aparece pra quem vende por data.)

E O CARD PASSA A MOSTRAR A PROPOSTA: "📄 nº 23 · aprovada", do lado do nome. Assim você sabe do que o cliente está falando antes mesmo de abrir a ficha.$txt$,
 timestamptz '2026-09-15 21:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'busca-na-fila-do-app';
