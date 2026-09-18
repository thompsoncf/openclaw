-- 284_novidade_negociacao_por_valores.sql
-- Negociação também quando o cliente responde ao preço.
--
-- O QUE MUDOU NA TELA (/painel/prospeccao, aba Régua): o gatilho de etapa ganhou uma
-- opção nova — "orçamento enviado, OU a equipe passou preço na conversa e o cliente
-- respondeu". Quem escolher essa opção na coluna de Negociação vê o card andar
-- sozinho quando o cliente responde a uma mensagem nossa com valores, mesmo sem
-- orçamento formal no sistema.
--
-- POR QUE. Regra do dono da Prime, 18/09/2026: "quando ainda não foi mandado
-- orçamento mas já tá tratando em valores" é Negociação. Medido nesse dia: 153 dos
-- 317 leads em Contatado já tinham recebido preço — a Prime manda o pacote com
-- valores na primeira resposta. Por isso o fato é a RESPOSTA do cliente ao preço
-- (107), e não o preço em si. Preço no vácuo continua Contatado.
--
-- O PORTÃO: `servico`. Quem vende produto não tem funil.
-- PRA QUEM: dono e gestor — é quem escolhe o gatilho da coluna.
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('negociacao-por-valores', 'novidade', 'servico', '{dono,gestor}',
 'O card vai para Negociação quando o cliente responde ao preço',
 'Além do orçamento enviado pelo sistema, a coluna de negociação pode andar sozinha quando a equipe passa valores na conversa e o cliente responde — sem esperar um orçamento formal.',
 '/painel/prospeccao/regua',
 $txt$A coluna de Negociação ganhou um segundo jeito de receber o card.

ONDE LIGAR
Régua do funil › na etapa de negociação, escolha o gatilho "orçamento enviado, OU a
equipe passou preço na conversa e o cliente respondeu". Ele inclui o gatilho antigo
de orçamento enviado — quem troca não perde nada.

O QUE CONTA COMO "TRATANDO EM VALORES"
Duas coisas ao mesmo tempo: alguém da equipe mandou uma mensagem com preço (R$,
pacote, valores, parcelamento, desconto), e o cliente respondeu DEPOIS dela. Preço
mandado e sem resposta não move o card — muita empresa passa valores logo na
primeira mensagem, e isso ainda é o começo da conversa, não uma negociação.

O QUE NÃO MUDA
A mão do vendedor continua mandando: se ele puxar o card de volta, o gatilho não
desfaz. E o card que já está em uma etapa à frente não volta.$txt$,
 now())
on conflict (chave) do nothing;
