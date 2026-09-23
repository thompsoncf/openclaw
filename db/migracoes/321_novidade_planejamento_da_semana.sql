-- 321_novidade_planejamento_da_semana.sql
-- O saldo do banco entra no painel, e a aba Empresa responde "sobra ou falta
-- pra semana". No Financeiro, a despesa da empresa aparece quebrada por centro.
--
-- O QUE MUDOU:
--   * Aba Empresa, card novo "Planejamento da semana", entre o DRE e as contas:
--     saldo nos bancos − contas atrasadas − contas a vencer em 7 dias = sobra
--     (ou falta). O saldo é digitado por banco (Sicoob e Banco do Nordeste, na
--     Prime), fica com a data em que foi informado e fica cinza a partir de 3
--     dias, pedindo pra atualizar. Faltando dinheiro e havendo conta a receber
--     vencida que cubra, a faixa diz que cobrar resolve.
--   * Financeiro, card Despesas: embaixo de Empresa/Pessoal, a despesa da
--     EMPRESA quebrada pelos centros de custo da conta, com "Sem centro" em
--     âmbar. Só aparece pra conta que tem centro de custo cadastrado.
--
-- POR QUE. Entrega 4 do plano aprovado pelo dono em 23/09/2026 (pedidos 2 e 3:
-- "criar um card com 3 tipos de despesas — fixa, eventual, investimento" e
-- "saldo da conta para planejamento de contas a pagar"). Os três tipos são os
-- centros de custo que a Prime já tem — nenhum centro foi criado ou mudado
-- ("não mexer em centro de custos", resposta dele). Open Finance de verdade
-- (etapa B) espera a escolha do agregador.
--
-- PRA QUEM: dono e gestor — são eles que veem a aba Empresa e o Financeiro da
-- empresa. Vendedor não tem essas telas.
--
-- O PORTÃO: `todos` — saldo, contas a pagar e centro de custo não são assunto
-- de nicho.
--
-- CONTAS ALCANÇADAS que já usam títulos (leitura em produção, 23/09/2026):
--   3 ZAQ - SISTEMAS IAs · 9 Ceaseiro · 16 SUPER FIT · 30 PC CONTABILIDADE ·
--   34 PRIME EVENTOS
-- A quebra por centro, dessas, só aparece hoje na 34 — é a única com centro de
-- custo cadastrado (13 centros, leitura no mesmo dia). As outras recebem o
-- planejamento da semana.
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('planejamento-da-semana', 'novidade', 'todos', '{dono,gestor}',
 'Sobra ou falta pra semana: o saldo do banco entrou no painel',
 'Informe o saldo de cada banco e a aba Empresa mostra quanto sobra (ou falta) depois das contas atrasadas e das que vencem em 7 dias. No Financeiro, a despesa da empresa aparece por centro de custo.',
 '/painel/empresa#planejamento',
 $txt$Na aba Empresa tem um card novo, "Planejamento da semana". Ele faz a conta que antes era de cabeça:

Saldo nos bancos
− contas a pagar atrasadas
− contas a pagar que vencem nos próximos 7 dias
= o que sobra (ou falta)

COMO INFORMAR O SALDO

Digite o saldo de cada banco no próprio card — um por banco (Sicoob, Banco do Nordeste…). Cada saldo mostra quando foi informado; passados 3 dias ele fica cinza e pede pra atualizar, porque saldo velho engana. Saldo negativo (cheque especial) pode: ele entra na conta como é.

Atualizar não apaga o anterior — fica o histórico. E se um banco entrou errado, o ✕ tira ele da soma sem apagar nada.

QUANDO FALTA

Se a semana fecha no vermelho e há dinheiro a receber já vencido que cobre a diferença, o card avisa: cobrar resolve.

NO FINANCEIRO

O card Despesas passa a mostrar a despesa da empresa por centro de custo (os centros que você já tem: despesa fixa, eventual, investimento…). O que foi lançado sem centro aparece em "Sem centro", em âmbar — é o que falta classificar.$txt$,
 timestamptz '2026-09-24 11:10:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'planejamento-da-semana';
