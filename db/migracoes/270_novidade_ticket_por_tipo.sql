-- 270_novidade_ticket_por_tipo.sql
-- O ticket por tipo de festa estava escondendo proposta. Corrigido.
--
-- ACHADO PELO DONO em 16/09/2026, olhando o Raio-X da conta dele: "dê uma olhada
-- nesse KPI, eu acho que está errado". Estava.
--
-- O DEFEITO. O período (setembro) filtrava a data de ENTRADA DO LEAD, e não a
-- data da PROPOSTA. Uma proposta feita este mês pra um lead que entrou no mês
-- passado simplesmente não existia pro cálculo. Na Prime isso escondia as duas
-- propostas de Aniversário de setembro — R$ 5.000 e R$ 8.600, esta última
-- FECHADA — e a tela dizia "Aniversário (20) sem proposta". Mesma coisa em
-- 15 anos, que tinha uma proposta de R$ 9.650. E estreitava o Casamento: o
-- R$ 7.900 era a média de duas das TRÊS propostas do mês (a de R$ 6.500 ficou de
-- fora porque o lead era de agosto).
--
-- A PROVA de que era defeito e não critério estava na mesma tela: o bloco de
-- propostas enviadas, logo acima, sempre filtrou pela data da proposta. Os dois
-- falavam das mesmas propostas e contavam períodos diferentes — o de cima dizia
-- seis, o de baixo mostrava três.
--
-- O QUE MUDOU NA TELA (/painel/raio-x, bloco "Tipo de festa e ticket")
-- 1) O ticket passa a somar TODA proposta feita no período, seja de que mês for
--    o lead.
-- 2) A linha mostra os DOIS números, separados e com nome: "38 leads · 3
--    propostas". Antes era só "(38)" colado no ticket, e lia-se como se os 38
--    tivessem dado aquele valor — quando o valor vinha de 2.
-- 3) "sem proposta" virou "sem proposta no período", que é o que a frase quer
--    dizer de verdade.
--
-- DOIS DEFEITOS MENORES no mesmo lugar, achados ao consertar o primeiro, os dois
-- sem efeito visível na Prime hoje mas que apareceriam sozinhos:
-- * a média era pesada pelo NÚMERO DE PROPOSTAS e não pelas que têm valor, o que
--   inflava um tipo com proposta sem preço. Só aparece quando dois tipos crus
--   caem no mesmo canônico ("Chá" e "Confraternização" viram "Outro").
-- * orçamento de VALOR ÚNICO (total em `setup_centavos`, sem `primeiro_ano`)
--   saía da média. O resto da casa lê `coalesce(primeiro_ano, setup)`; só aqui
--   era o primeiro sem a rede, e a tela diria "sem proposta" com uma na mão.
--
-- O PORTÃO: `eventos`. O bloco só existe no perfil de eventos (`raio_x_perfil`,
-- blocos `tipos`), e num nicho de mensalidade "tipo de festa" não quer dizer
-- nada. Medido nas duas pontas (§6): na 34 os números acima são reais; na 3
-- (consultoria) o bloco não aparece, então não há o que conferir.
--
-- TIPO `mudanca`, e não "correcao": a tabela só aceita 'novidade' e 'mudanca'
-- (check da 174), e o que vale aqui é o que a pessoa vê — o número na tela dela
-- muda. Errei nisto uma vez e o CI pegou; o check é justamente pra pegar.
--
-- PRA QUEM: dono e gestor — o Raio-X do dono é a tela deles.
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('ticket-por-tipo-conta-a-proposta-do-mes', 'mudanca', 'eventos', '{dono,gestor}',
 'O ticket por tipo de festa estava escondendo proposta',
 'O Raio-X passou a contar toda proposta feita no período ao calcular o ticket por tipo de festa — antes ele só via as de leads que tinham entrado no mesmo período.',
 '/painel/raio-x',
 $txt$Você olhou o bloco "Tipo de festa e ticket" e achou que estava errado. Estava, e obrigado por avisar.

O QUE ACONTECIA: o filtro de período valia pela data em que o LEAD entrou, não pela data da PROPOSTA. Então uma proposta feita em setembro para um cliente que chegou em agosto não entrava na conta de setembro.

Na sua tela isso significava: "Aniversário (20) sem proposta" — quando existiam duas propostas de Aniversário feitas no mês, de R$ 5.000 e R$ 8.600, esta última já fechada. "15 anos sem proposta", com uma de R$ 9.650 no mês. E o Casamento aparecia com R$ 7.900, média de duas das três propostas do mês.

AGORA o ticket conta toda proposta feita no período, seja de que mês for o cliente.

E A LINHA MOSTRA OS DOIS NÚMEROS, com nome: "38 leads · 3 propostas". Antes era só "(38)" ao lado do valor, e dava pra entender que aqueles 38 tinham dado aquele ticket — quando o ticket vinha de 3. Os dois números respondem coisas diferentes: quantos procuraram esse tipo de festa, e quanto se está cobrando por ele.

Uma observação pra leitura do dia a dia: um número pequeno de propostas ao lado de muitos leads não é erro da tela — é a proporção real entre quem procura e quem recebe proposta. Vale olhar.$txt$,
 timestamptz '2026-09-16 23:30:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'ticket-por-tipo-conta-a-proposta-do-mes';
