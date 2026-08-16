-- 161_orcamento_sinal.sql
-- O sinal no orçamento — a outra metade da pré-reserva de data (ver 160).
--
-- O gerador de parcelas do orçamento de evento já escreve, na primeira linha,
-- "Sinal — confirma a reserva da data". O VALOR, portanto, já vivia em
-- `parcelas[0]`. O que não existia em lugar nenhum era saber se ele FOI PAGO — e
-- é exatamente isso que decide se a data é do cliente ou só está segurada.
--
-- `sinal_centavos` é o valor congelado no momento em que a data foi pré-reservada
-- (parcelas continuam editáveis até a assinatura; a reserva não pode depender de
-- um número que muda depois). `sinal_pago_em` é o carimbo do dono confirmando —
-- manual de propósito: ninguém cobra nada aqui, o Pix cai onde já cai hoje e o
-- botão só registra. Se um dia a cobrança automática existir, ela aperta este
-- mesmo campo.
--
-- Aditivo e idempotente.
alter table public.orcamentos add column if not exists sinal_centavos int;
alter table public.orcamentos add column if not exists sinal_pago_em  timestamptz;

-- rollback:
--   alter table public.orcamentos drop column if exists sinal_centavos;
--   alter table public.orcamentos drop column if exists sinal_pago_em;
