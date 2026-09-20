-- 303_tempo_tela_conexoes.sql
-- QUANTAS CONEXÕES a tela abriu, ao lado de quantas consultas fez.
--
-- POR QUÊ. A medição de 20/09/2026 mostrou 46 consultas na Fila a ~90 ms cada —
-- e 90 ms é a distância Oregon↔us-east-1, não o custo de ler uma linha. Só que
-- "consulta" aqui conta DUAS coisas diferentes:
--
--   o trabalho   — os selects que desenham a tela;
--   o pedágio    — o `SELECT 1` que o pool dispara a cada bloco
--                  `with pool.connection()` pra ver se a conexão do pooler
--                  ainda está viva (db/conexao.py, `check=check_connection`).
--
-- Os dois custam a mesma travessia, mas têm consertos opostos: o primeiro se
-- corta juntando consultas, o segundo se corta fazendo a requisição inteira usar
-- UMA conexão. Sem separar os dois, otimizar vira chute — e foi por chute que o
-- ajuste de toque do mesmo dia não mudou nada pra quem usa.
alter table public.tempo_tela
    add column if not exists conexoes integer not null default 0;
