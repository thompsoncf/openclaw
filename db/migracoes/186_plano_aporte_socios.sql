-- 186_plano_aporte_socios.sql
-- O grupo 7 tinha o sócio TIRANDO e não tinha o sócio PONDO.
--
-- 7.1.02 Distribuição de Lucros já existia — dinheiro saindo pros donos. O
-- caminho de volta não tinha conta nenhuma, e por isso os aportes lançados em
-- produção foram parar em categoria de RECEITA: conferido em 24/08/2026, os três
-- lançamentos com "aporte" na descrição caíram em três categorias diferentes —
-- "Outros" (R$ 2.500), "Investimentos" (R$ 500) e "Presentes" (R$ 200).
--
-- Aporte não é faturamento: é o dono cobrindo caixa. Somado à receita, ele faz o
-- número que mede o negócio subir por um motivo que não é o negócio.
--
-- POR QUE natureza='receita' NUM GRUPO DE DESPESA
--
-- O grupo 7 ("Não Operacional / Investimentos") tem papel 'despesa' no
-- GRUPOS_DRE, e a DRE fazia `sinal = 1 if papel == 'receita' else -1`. Com isso
-- um aporte entraria NEGATIVO. O finance/empresa.py passa a tirar o sinal da
-- natureza DA CONTA, não do papel do grupo — que é como um grupo não operacional
-- de verdade funciona: ele tem os dois lados.
--
-- A mudança é preservadora pro que já existe: nos grupos 1 a 6 a natureza de cada
-- conta já casa com o papel do grupo, e o grupo 7 até aqui só tinha despesa.
--
-- Reverter:
--   delete from public.plano_contas where codigo = '7.1.05';

insert into public.plano_contas (codigo, nome, grupo, natureza, ordem) values
  ('7.1.05', 'Aporte de Sócios', 7, 'receita', 39)
on conflict (codigo) do nothing;
