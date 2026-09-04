-- 198_prospeccao_evento_origem.sql
-- O CARD LÊ A CONVERSA: de onde veio a data do evento, e a prova.
--
-- POR QUE. Medido na Prime (conta 34) em 04/09/2026: dos 246 leads sem data do
-- evento, 121 já tinham dito a data NA CONVERSA ("casamento, 13 de fevereiro",
-- "14/11/2026 15 anos 120 convidados"), 83 os convidados, 121 o tipo. O agente
-- lê isso quando é ele quem responde; onde quem responde é gente, nada lia.
-- O leitor (finance/evento_leitor.py) passa a ler toda mensagem do cliente e
-- preencher o que está vazio — e estas colunas dizem ao vendedor DE ONDE veio o
-- que está no card, com o trecho exato, pra ele confirmar ou corrigir.
--
--   evento_origem   conversa · agente · orcamento · mao · confirmado
--   evento_trecho   o pedaço da mensagem de onde o leitor tirou (a prova, no balão)
--   evento_pista    o que o leitor ouviu mas não gravou ("falou de março",
--                   "falou de 20 fev") — o card mostra em âmbar
--   evento_lido_em  quando o leitor passou por último (pra ler só o que é novo)
--
-- Aditivo e idempotente. O backfill do que já está preenchido marca a origem:
-- quem tem orçamento amarrado com data veio do orçamento (migração 197).
alter table public.prospeccao add column if not exists evento_origem  text;
alter table public.prospeccao add column if not exists evento_trecho  text;
alter table public.prospeccao add column if not exists evento_pista   text;
alter table public.prospeccao add column if not exists evento_lido_em timestamptz;

update public.prospeccao p
   set evento_origem = 'orcamento'
  from public.orcamentos o
 where o.id = p.orcamento_id and p.evento_em is not null and p.evento_origem is null;
