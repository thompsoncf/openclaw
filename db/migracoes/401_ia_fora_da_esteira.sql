-- 401_ia_fora_da_esteira.sql
-- CADA TRILHA COM A SUA REGRA (decisão do dono em 27/09/2026, finance/esteira.py,
-- finance/follow_up.py e finance/ia_insiste.py).
--
-- O que estava errado, medido na produção (só leitura) antes de mexer: a esteira e o
-- follow-up tratavam o membro IA (o dono da regra por número com a IA ligada) como
-- vendedor de carne e osso — 9 entradas na esteira, 66 avisos de cobrança, e um lead
-- dele fechado como "sem tratativa" pela esteira. E o resgate, ao passar um lead que
-- estava na esteira de um vendedor, fazia a linha fechar como 'falou': o placar do
-- vendedor ganhava a mensagem que a IA mandou.
--
-- 1. `follow_up_esteira.resolucao` ganha dois valores:
--      'historico' — o vendedor escreveu no histórico do lead (o aviso da esteira
--                    sempre disse "o que não está escrito não conta"; agora o que
--                    está escrito conta);
--      'resgate'   — o lead foi pro resgate da IA. Não é crédito do vendedor.
-- 2. `chip_regra.ia_insiste`: a IA do número insiste quando o cliente some (2º toque
--    no dia 3, último no dia 7, perdido no dia 10) — a mesma regra do resgate, pro
--    lead que ela atende desde o primeiro "oi". Nasce DESLIGADA.
-- 3. `chip_regra_leads` guarda os toques dessa insistência: quantos, quando, e o
--    silêncio a que eles se referem (a última mensagem do cliente — se ele fala de
--    novo, a conta recomeça).
--
-- Não toca em `canais_config` nem em nada da conexão (CLAUDE.md §1). Aditiva e
-- idempotente. As três tabelas são pequenas (centenas de linhas): a troca do check
-- e as colunas com default constante não reescrevem nada. O `if exists` é pra quem
-- aplica esta migração com só uma das pontas (os testes da esteira não têm a regra
-- por número, e os da regra não têm a esteira).

alter table if exists public.follow_up_esteira drop constraint if exists follow_up_esteira_resolucao_ck;
alter table if exists public.follow_up_esteira add constraint follow_up_esteira_resolucao_ck
  check (resolucao is null or resolucao in ('falou','moveu','fechou','cliente_voltou',
                                            'historico','resgate'));

alter table if exists public.chip_regra
  add column if not exists ia_insiste boolean not null default false;

alter table if exists public.chip_regra_leads
  add column if not exists toques         smallint not null default 0,
  add column if not exists toque_em       timestamptz,
  add column if not exists silencio_desde timestamptz,
  add column if not exists perdido_em     timestamptz;

-- rollback:
--   alter table public.chip_regra_leads drop column if exists toques,
--     drop column if exists toque_em, drop column if exists silencio_desde,
--     drop column if exists perdido_em;
--   alter table public.chip_regra drop column if exists ia_insiste;
--   update public.follow_up_esteira set resolucao='falou' where resolucao in ('historico','resgate');
--   alter table public.follow_up_esteira drop constraint if exists follow_up_esteira_resolucao_ck;
--   alter table public.follow_up_esteira add constraint follow_up_esteira_resolucao_ck
--     check (resolucao is null or resolucao in ('falou','moveu','fechou','cliente_voltou'));
