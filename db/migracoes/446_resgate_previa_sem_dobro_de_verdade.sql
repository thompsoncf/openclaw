-- 446_resgate_previa_sem_dobro_de_verdade.sql
-- A 445 criou o índice único que devia impedir a prévia em dobro, mas o índice
-- NUNCA existiu de verdade em produção: as duas linhas duplicadas que já tinham
-- sido gravadas pro lead #1167 (Rozalia, Prime) — a MESMA corrida entre os 2
-- workers, de novo, na manhã de 28/09/2026, antes da 445 entrar no ar — fizeram
-- o `CREATE UNIQUE INDEX` falhar com "could not create unique index ... is
-- duplicated". E o runner (`db/aplicar_migracoes.py`) tinha um bug que
-- confundia esse erro com "índice já existe": qualquer mensagem contendo a
-- palavra "duplicate" era tratada como sinal de que a migração já tinha
-- rodado antes — e "is duplicated" (dado duplicado bloqueando a criação)
-- CONTÉM "duplicate" como substring. A migração 445 ficou marcada como
-- concluída em `schema_migrations` sem o índice existir. Conferido em
-- produção (só leitura) em 28/09/2026: `schema_migrations` tinha a 445,
-- `pg_indexes` não tinha o índice.
--
-- Esta migração:
-- 1. Apaga as linhas duplicadas de `tipo='previa'`, mantendo a mais antiga
--    (menor id) de cada grupo (conta, lead, ref_em) — é registro de envio
--    (auditoria), não conversa nem dado que o cliente vê; a linha mantida já
--    tem o texto real que foi mandado.
-- 2. Cria o índice único (idempotente: `if not exists`).
--
-- O runner (`db/aplicar_migracoes.py`) também foi corrigido nesta mesma
-- entrega pra nunca mais confundir os dois erros — sem isso, esta mesma
-- migração cairia no mesmo bug de novo se rodasse antes do deploy do código
-- novo (o SQL sozinho não muda o runner).
--
-- Não toca em `canais_config`, sessão ou conexão (CLAUDE.md §1). Aditiva e
-- idempotente: rodar de novo não apaga nada (não sobra duplicata) nem falha
-- (índice já existe).

delete from public.resgate_envios a
  using public.resgate_envios b
 where a.tipo = 'previa'
   and b.tipo = 'previa'
   and a.conta_id = b.conta_id
   and a.prospeccao_id = b.prospeccao_id
   and a.ref_em is not distinct from b.ref_em
   and a.id > b.id;

create unique index if not exists resgate_envios_previa_unica
  on public.resgate_envios (conta_id, prospeccao_id, ref_em)
  where tipo = 'previa';

-- rollback:
--   drop index if exists public.resgate_envios_previa_unica;
--   -- as linhas apagadas não voltam (é registro de auditoria, sem dado do
--   -- cliente perdido: a prévia duplicada era o mesmo texto mandado 2x).
