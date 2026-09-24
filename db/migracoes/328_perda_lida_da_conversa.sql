-- 328_perda_lida_da_conversa.sql
-- O motivo da perda LIDO DA CONVERSA, ao lado do que o vendedor marcou.
--
-- POR QUE. Medido na Prime em 24/09/2026: dos 38 leads perdidos, 18 estavam sem
-- motivo nenhum, e "preço" nunca tinha sido marcado — mas aparecia em 5 conversas
-- ("é quase 10 mil só o espaço?"). E 10 dos 38 nem queriam festa: currículo,
-- fornecedor, aluguel de copo e cadeira, pedido de doação. Lendo as conversas, os
-- 38 tinham motivo.
--
-- O QUE ENTRA. Colunas NOVAS e só elas: `finance.motivo_lido` escreve aqui e em
-- nenhum outro lugar. `perda_motivo` continua sendo do vendedor — o que ele marca
-- vale mais, e a leitura nunca passa por cima (a tela usa a leitura só onde o
-- vendedor não disse nada, ou disse "Outro").
--
--   perda_lida            chave de `funil_motivos_perda` da conta, ou uma das duas
--                         que não cabem ali: 'nao_era_cliente' e 'sem_conversa'
--   perda_lida_trecho     a frase do cliente que justifica (curta) — é o que a ficha
--                         mostra ao lado do selo 💬 lido
--   perda_lida_em         quando leu; se o lead for perdido DE NOVO depois
--                         (`perda_em` mais novo), lê outra vez
--   perda_lida_tentativas quantas vezes a leitura falhou; passa de 3, desiste
--
-- Aditivo: colunas anuláveis, sem reescrever a tabela. Nada existente muda.

alter table public.prospeccao
  add column if not exists perda_lida text,
  add column if not exists perda_lida_trecho text,
  add column if not exists perda_lida_em timestamptz,
  add column if not exists perda_lida_tentativas integer not null default 0;

-- rollback:
--   alter table public.prospeccao drop column if exists perda_lida,
--     drop column if exists perda_lida_trecho, drop column if exists perda_lida_em,
--     drop column if exists perda_lida_tentativas;
