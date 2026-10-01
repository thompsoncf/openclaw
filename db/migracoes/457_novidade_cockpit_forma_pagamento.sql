-- 457_novidade_cockpit_forma_pagamento.sql
-- O seletor de forma de pagamento nas parcelas do orçamento do app (pedido do
-- dono em 01/10/2026: "a forma de pagamento pelo cockpit tem que ser igual o
-- orçamento"), seguindo a seção 5 do CLAUDE.md.
--
-- O QUE MUDOU NA TELA: no orçamento do app do vendedor, cada parcela ganha a
-- forma de pagamento (Pix, Cartão de crédito, Cartão de débito, Boleto,
-- Dinheiro, Transferência ou Outro) — a mesma lista do computador. Até aqui o
-- app gravava toda parcela com a forma em branco.
--
-- No mesmo PR, sem mudar tela: a data do evento e os vencimentos que o vendedor
-- escreve no app passam a abrir preenchidos no computador (antes o orçamento
-- nº 47 da Prime abria lá sem data e sem vencimento nenhum). O corpo conta,
-- porque foi a reclamação de quem usa.
--
-- PÚBLICO `eventos`: parcelas e data do evento só existem no orçamento desse
-- modo (`vendas.modo_por_nicho`, o mesmo portão de `contrato.tem_contrato`).
-- PRA QUEM: vendedor — é quem monta o orçamento no app. Sem resumo: é ajuste de
-- tela interna, não vai pro site.
--
-- Aditiva e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, link, corpo, publicado_em) values
('cockpit-forma-de-pagamento', 'mudanca', 'eventos', '{vendedor}',
 'Forma de pagamento nas parcelas do orçamento',
 '/cockpit',
 $txt$No orçamento do app, cada parcela agora tem a forma de pagamento: Pix, Cartão de crédito, Cartão de débito, Boleto, Dinheiro, Transferência ou Outro — a mesma lista do computador. A forma escolhida fica marcada pra próxima parcela, então um plano todo no boleto se monta escolhendo uma vez só.

E a data do evento e os vencimentos que você escreve no app agora aparecem preenchidos quando o orçamento é aberto no computador.$txt$,
 timestamptz '2026-10-01 18:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'cockpit-forma-de-pagamento';
