-- 409_resgate_teste_chip.sql
-- O "TESTAR COMIGO" GUARDA O CHIP (decisão do dono em 27/09/2026, mockup
-- docs/mockups/teste_resgate_chip_certo.html, finance/resgate.py).
--
-- O que aconteceu: o dono escreveu pro chip Thiago pra testar o ZAQ SDR e quem
-- respondeu foi o CP Zarb — era o teste do resgate, ainda aberto, que pegava a
-- mensagem do supervisor em QUALQUER chip e respondia sempre pelo principal.
--
-- `resgate_teste.chip_id`: o chip por onde o teste fala — o da conversa do lead do
-- teste, o mesmo por onde a retomada de verdade sairia. Nulo = o principal (a própria
-- empresa), que é o que todo teste anterior foi. É por ele que a mensagem do
-- supervisor só vira fala do "cliente" no chip do teste.
--
-- Não toca em `canais_config` nem em nada da conexão (CLAUDE.md §1). Aditiva e
-- idempotente; a tabela tem uma linha por conta, no máximo.

alter table if exists public.resgate_teste add column if not exists chip_id bigint;

-- rollback:
--   alter table if exists public.resgate_teste drop column if exists chip_id;
