-- 445_resgate_previa_sem_dobro.sql
-- A prévia do Ensaio do resgate saiu em dobro pro supervisor (achado em produção,
-- 28/09/2026, na Prime): o lead #1167 (Rozalia) recebeu a MESMA prévia duas vezes,
-- 0,7 segundo de diferença (`resgate_envios` ids 25 e 26, mesmo `ref_em`).
--
-- A CAUSA: o serviço roda com 2 processos (`uvicorn --workers 2`, render.yaml), cada
-- um com o próprio relógio do resgate, e a trava que devia impedir os dois de mandar
-- junto (`pg_try_advisory_lock`, `finance/resgate.py`) é uma trava de SESSÃO — e a
-- conexão do app passa pelo pooler do Supabase (`db/conexao.py` já desliga prepared
-- statements por causa dele: "é o que faz o pooler do Supabase funcionar sem erros
-- misteriosos"). No modo de transação do pooler, cada instrução autocommitada pode
-- ir pra uma sessão física diferente do Postgres — a trava que um worker pensa que
-- segurou pode já ter sumido quando o outro pergunta, e os dois recebem "peguei".
--
-- O RESGATE JÁ TINHA O CONSERTO CERTO EM DOIS LUGARES (`_passar`, que troca o
-- vendedor do lead só se ninguém mexeu nele, e `_um_toque`, que reivindica o passo
-- antes de mandar) — os dois são reivindicações ATÔMICAS no próprio UPDATE, que não
-- dependem da trava de sessão. Só a prévia do Ensaio lia "já mandei?" ANTES de
-- mandar, sem reivindicar nada — e essa leitura, sem a trava, não impede a corrida.
--
-- Este índice fecha a mesma porta pra prévia: só uma linha por (conta, lead,
-- ref_em) com tipo='previa' consegue existir. O segundo worker que tentar cai no
-- "on conflict" e nem chega a mandar (finance/resgate.py, `supervisor()`, o
-- INSERT ... ON CONFLICT ... DO NOTHING antes do envio).
--
-- Não toca em `canais_config` nem em nada da conexão (CLAUDE.md §1). Aditiva e
-- idempotente.

create unique index if not exists resgate_envios_previa_unica
  on public.resgate_envios (conta_id, prospeccao_id, ref_em)
  where tipo = 'previa';

-- rollback:
--   drop index if exists public.resgate_envios_previa_unica;
