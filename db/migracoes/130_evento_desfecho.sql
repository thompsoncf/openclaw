-- 130_evento_desfecho.sql
-- Desfecho do compromisso (separado do status de agenda/cancelou): em aberto até
-- a pessoa marcar (na tela ou falando com o Zaq) que a reunião REALIZOU ou NÃO
-- REALIZOU. Cancelado continua sendo cancelado (status já resolve isso) — este
-- campo é só pra quem ficou "ativo" na agenda mas passou e ninguém disse o que
-- aconteceu. Alimenta as sugestões de "reaproveitar" (cancelado ou não_realizado
-- = compromisso que não aconteceu, candidato a remarcar em vez de recriar do
-- zero). Idempotente.

alter table public.eventos_agenda add column if not exists desfecho text;
alter table public.eventos_agenda drop constraint if exists eventos_agenda_desfecho_check;
alter table public.eventos_agenda
  add constraint eventos_agenda_desfecho_check
  check (desfecho is null or desfecho in ('realizado','nao_realizado'));
