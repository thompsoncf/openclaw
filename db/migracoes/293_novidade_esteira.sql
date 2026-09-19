-- 293_novidade_esteira.sql
-- A esteira da cobrança: 10 por dia, com nome, no WhatsApp.
--
-- O QUE MUDOU NA TELA (/painel/prospeccao, aba Régua): no bloco Estado aparece um
-- interruptor novo — "Esteira da cobrança". Ligado, cada vendedor recebe todo dia
-- de manhã, no WhatsApp dele, os leads que precisa tratar — COM OS NOMES — mais o
-- resumo do que ele fez e do que não fez no dia anterior.
--
-- POR QUE OS NOMES, E POR QUE O WHATSAPP. Medido na conta 34 em 19/09/2026: o app
-- do vendedor teve 19 acessos em 19 dias entre quatro pessoas, enquanto 1.191 das
-- 1.505 mensagens do mês saíram do WhatsApp Web. O aviso antigo dizia "10 leads
-- esperando" e mandava um link pro app — batia na porta errada. Com nome, o
-- vendedor procura a pessoa no WhatsApp que já está aberto e responde.
--
-- POR QUE 10 POR DIA. O acervo da Prime tinha 369 leads parados. Cobrar todos de
-- uma vez é não cobrar nada. Dez por dia, por vendedor, faz o acervo inteiro ser
-- tratado em cerca de três semanas — com cada lead julgado por uma pessoa.
--
-- O DIA 7. Cobrança no dia 1, no dia 3 e no dia 7. No sétimo o aviso avisa que é o
-- último dia; sem tratativa até o fim do expediente, o lead fecha com o motivo
-- "prazo vencido sem tratativa" — que é diferente de "o cliente não respondeu".
--
-- O PORTÃO: `servico`. Quem vende produto não tem funil nem cobrança de vendedor.
-- PRA QUEM: dono e gestor — é quem liga. O vendedor recebe, não configura.
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('esteira-da-cobranca', 'novidade', 'servico', '{dono,gestor}',
 'A cobrança agora chega com nome, no WhatsApp do vendedor',
 'Cada vendedor recebe todo dia os leads que precisa tratar, com os nomes, no WhatsApp — e um resumo do que fez e do que não fez. São dez por dia, com cobrança no dia 1, 3 e 7.',
 '/painel/prospeccao/regua',
 $txt$A cobrança deixou de mandar o vendedor procurar.

COMO LIGAR
Régua do funil › Estado › "Esteira da cobrança". Nasce Desligada. Em Observando
ela entra, cobra e resume — mas não fecha ninguém no dia 7.

COMO FUNCIONA
Todo dia, os dez leads mais antigos de cada vendedor entram na esteira dele. A
partir daí ele é cobrado três vezes: no dia 1, no dia 3 e no dia 7. O aviso chega
no WhatsApp com os NOMES das pessoas — não com uma contagem e um link.

Junto vai o resumo do dia anterior: quantos ele tratou e quantos continuam na
esteira dele.

O QUE CONTA COMO TRATAR
Falar com o cliente, inclusive pelo WhatsApp normal da empresa — o sistema
enxerga a mensagem mesmo que ela não passe por aqui. Mover o card também conta. E
fechar o lead escrevendo o motivo conta como tratado, porque é uma decisão.

Se o cliente voltar a falar, o lead sai da cobrança sozinho: quem respondeu
deixou de estar parado.

O DIA 7
No sétimo dia o aviso muda de tom e diz quais leads fecham hoje. Sem tratativa até
o fim do expediente, eles vão para Perdido com o motivo "prazo vencido sem
tratativa" — registrado assim de propósito, separado de "o cliente não respondeu".
Um culpa o processo, o outro culpa o cliente, e misturar os dois estraga o
relatório de perdas.

QUEM NUNCA ENTRA
Lead com o cliente esperando resposta nossa. Esse não é dívida do vendedor com o
prazo — é dívida nossa com o cliente, e aparece na Fila, não na cobrança.$txt$,
 now())
on conflict (chave) do nothing;
