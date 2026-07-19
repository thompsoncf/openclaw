-- 077_prospeccao_receita.sql
-- Guarda o pacote rico da Receita/CNPJá (nome fantasia, situação, abertura,
-- capital, natureza, endereço, atividades, inscrição estadual…) num jsonb,
-- pra mostrar na ficha sem precisar de uma coluna por campo. Idempotente.

alter table public.prospeccao add column if not exists receita jsonb;
