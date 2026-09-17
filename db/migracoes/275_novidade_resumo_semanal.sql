-- 275_novidade_resumo_semanal.sql
-- A semana da empresa chega por e-mail, toda segunda.
--
-- O PEDIDO (dono, 17/09/2026): "vamos criar o email pro gestor, semana de
-- acompanhamento da sua empresa". Mockup aprovado em
-- docs/mockups/email_semanal_do_gestor.html; motor na migração 274.
--
-- O QUE MUDOU NA TELA
-- 1) /painel/prospeccao/comunicacao?aba=agente (a engrenagem → 🤖 Agente IA) ganha
--    o card "Resumo semanal por e-mail": o interruptor, o campo de e-mails de
--    gestor, se cada vendedor recebe a parte dele e o dia. Salva no MESMO botão do
--    agente — a aba é um formulário só.
-- 2) Quem ligar passa a receber, toda segunda às 9h, o funil da semana fechada.
--
-- TRÊS DESTINATÁRIOS, TRÊS E-MAILS, como o dono escolheu:
--   dono ...... tudo, com o resultado de cada vendedor PELO NOME;
--   gestor .... a mesma semana com o total da equipe, sem nomes;
--   vendedor .. só a carteira dele, sem comparação com colega nenhum.
--
-- "FECHOU" É CONTRATO ASSINADO, e este é o ponto mais importante da entrega. O
-- primeiro rascunho media pelo funil (`para='ganho'`) e ia anunciar "nenhuma venda
-- fechada, o Pedro e a Jacqueline não propuseram nenhum" numa semana em que os dois
-- fecharam R$ 19.000. A Prime não move o card pra Ganho: assina o contrato e deixa
-- o lead onde estava — dois dos quatro contratos daquela semana seguem em
-- "Proposta". Um número medido no lugar errado não erra sozinho; ele acusa alguém.
--
-- NASCE DESLIGADO, e semana sem movimento não vira e-mail. As duas regras nasceram
-- junto com o recurso, e não depois: no dia anterior a medição dos avisos mostrou
-- 30 publicados em 7 dias, o dono com 47 por ler e ZERO lidos. Canal que fala
-- quando não tem o que dizer ensina a ser ignorado.
--
-- O PORTÃO: `servico`. É sobre funil, vendedor e contrato — existe em qualquer
-- nicho que venda serviço e não existe em conta de produto. Dentro do e-mail o
-- nicho manda (§6): "visitou o espaço" e "festa em menos de 30 dias" só aparecem
-- em conta que vende data; a de mensalidade recebe o mesmo caminho sem uma palavra
-- de festa.
--
-- PRA QUEM: dono e gestor (é o resumo da empresa deles) e vendedor (que passa a
-- receber a parte dele, se a conta ligar).
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('resumo-semanal-por-email', 'novidade', 'servico', '{dono,gestor,vendedor}',
 'A semana da sua empresa agora chega por e-mail',
 'Toda segunda de manhã, um e-mail com o caminho que a semana fez — de quantos clientes entraram até quantos contratos foram assinados — e o que ficou travado.',
 '/painel/prospeccao/comunicacao?aba=agente',
 $txt$Toda segunda de manhã, um e-mail com a semana que passou. Não é um painel: é uma página que você lê no celular, tomando café, e já sabe o que a semana pede.

O QUE VEM NELE:

O CAMINHO DA SEMANA, etapa por etapa — quantos entraram, quantos visitaram o espaço, quantos receberam proposta, quantos receberam contrato, quantos assinaram, quantos pagaram o sinal. Cada número ao lado do da semana anterior, porque "2 propostas" sozinho não é bom nem ruim.

O QUE ESTÁ TRAVADO: festas perto da data e ainda sem contrato, propostas paradas em rascunho, contrato enviado e não assinado, título vencido.

POR VENDEDOR, com o que cada um recebeu ao lado do que fez — porque 23 clientes e nenhum contrato conta uma história bem diferente de 2 clientes e nenhum contrato.

E OS PRÓXIMOS 7 DIAS: as visitas marcadas e as festas que acontecem.

UMA COISA IMPORTANTE SOBRE O NÚMERO DE VENDAS: o resumo conta CONTRATO ASSINADO, não a etapa do funil. Se um cliente assinou e o card dele continua em "Proposta" no quadro, o e-mail conta a venda do mesmo jeito — porque o contrato é o fato.

COMO LIGAR: Prospecção → engrenagem → Agente IA → "Resumo semanal por e-mail". Ali você também cadastra e-mails de gestores (sócio, contador, quem acompanha), escolhe se cada vendedor recebe a parte dele e se prefere segunda de manhã ou sexta à tarde.

Quem estiver cadastrado como gestor recebe a mesma semana, mas com o total da equipe em vez do resultado de cada pessoa pelo nome — esse fica só no seu. E e-mail cadastrado ali só recebe o resumo: não entra no painel e não vê lead nenhum.

Semana sem nenhum movimento não gera e-mail. Se não teve nada, não tem o que dizer.$txt$,
 timestamptz '2026-09-17 20:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'resumo-semanal-por-email';
