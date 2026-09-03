-- 194_assinar_antes_do_sinal.sql
-- A ordem entre o sinal e a assinatura vira escolha da empresa.
--
-- O QUE ACONTECEU
-- Em 02/09 um cliente da Prime quis LER o contrato antes de mandar a entrada. O
-- produto já permitia — o contrato nasce na aprovação do orçamento e, desde
-- 01/09, o sinal deixou de travar a assinatura. O que empurrava o vendedor para a
-- outra ordem era o FUNIL: o botão verde é um só por linha, e `_ORDEM_ACAO` põe
-- `sinal` na frente de `assinar`. Com a data pré-reservada, ele mostra "Sinal
-- recebido" — e mandar o contrato antes virava um caminho que ninguém via.
--
-- Decidido com o dono em 03/09: vira PARÂMETRO POR CONTA, e ele liga.
--
-- POR QUE COLUNA, E NÃO UMA CHAVE EM `regras`
-- `contrato_modelo.regras` é um jsonb de NÚMEROS QUE ENTRAM NO TEXTO do contrato
-- — sinal_pct, multa_cancelamento, tolerancia_min — cada um preenchendo um
-- {regra.x} de uma cláusula. Isto aqui não preenche cláusula nenhuma: muda a
-- ordem em que o funil pede as coisas. Guardar as duas naturezas no mesmo saco
-- faria a tela de regras oferecer um "campo" que nenhuma cláusula usa.
--
-- POR QUE EM `contrato_modelo`
-- É a tabela POR CONTA do nicho de eventos, e a mesma tela que o dono já abre pra
-- mexer nas regras do contrato dele. Conta sem linha aqui simplesmente não tem o
-- parâmetro — e `false` é o certo pra ela: a ordem de hoje continua valendo pra
-- quem não pediu para mudar.
--
-- O QUE **NÃO** MUDA (decisão do dono, 03/09): o prazo da pré-reserva continua
-- contando da APROVAÇÃO. Assinar antes de pagar alonga o tempo com a data
-- segurada sem dinheiro; manter o prazo de hoje é o que impede esse tempo de
-- crescer sozinho.
--
-- Aditivo e idempotente. Default `false` = comportamento de hoje, então nenhuma
-- conta muda de fluxo por causa desta migração.

alter table public.contrato_modelo
  add column if not exists assinar_antes_do_sinal boolean not null default false;

comment on column public.contrato_modelo.assinar_antes_do_sinal is
  'true = o funil pede a assinatura do contrato ANTES do sinal. O prazo da '
  'pré-reserva continua contando da aprovação nos dois casos.';

-- rollback:
--   alter table public.contrato_modelo drop column if exists assinar_antes_do_sinal;
