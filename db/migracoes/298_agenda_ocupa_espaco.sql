-- 298_agenda_ocupa_espaco.sql
-- A resposta do dono sobre um compromisso ocupar ou não o espaço.
--
-- POR QUE A COLUNA EXISTE. Desde o #757 o calendário responde "essa data está
-- livre?" derivando de quatro sinais (pré-reserva, tipo pessoal/fornecedor,
-- visita, orçamento ou tipo de festa). Sobram os que nenhum sinal alcança, e
-- para esses a tela diz "a conferir" em vez de chutar — porque chutar pelo
-- título já se provou errado: na Prime, "REUNIÃO COM ENGENHEIRA" não ocupa o
-- espaço e "Reunião Política - Bianca - Pedro" ocupa (teve sinal de R$ 750).
-- Marcar a primeira como vendida tiraria um dia do ar.
--
-- Esta coluna é onde a resposta fica. NULO é o estado normal e honesto:
-- "ninguém disse". True/false só aparece depois que uma pessoa respondeu, e
-- então vence toda a derivação (`finance.agenda.estado_da_data`, passo 1).
--
-- ADITIVA E SEM PADRÃO. Não existe `default false`: um padrão aqui apagaria a
-- diferença entre "respondido: não ocupa" e "ninguém respondeu", que é
-- justamente a diferença que fez a coluna nascer.
--
-- Na Prime (conta 34) em 19/09/2026 são CINCO compromissos nesse estado, de 84
-- ativos — 6%. Nenhuma linha é alterada por esta migração.

alter table public.eventos_agenda
  add column if not exists ocupa_espaco boolean;

comment on column public.eventos_agenda.ocupa_espaco is
  'Resposta humana: este compromisso ocupa o espaço? NULO = ninguém disse, e aí '
  'finance.agenda.estado_da_data deriva. Preenchido, vence a derivação.';

-- rollback:
--   alter table public.eventos_agenda drop column if exists ocupa_espaco;
