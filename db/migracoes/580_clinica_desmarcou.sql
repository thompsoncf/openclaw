-- 580_clinica_desmarcou.sql
-- CRM da clínica, entrega 2c (docs/mockups/clinica_crm_telas.html, seções 04 e 05, aprovado
-- em 01/10/2026; decisão C do dono em 02/10/2026). finance/clinica_agenda.py.
--
-- QUEM DESMARCOU. Dois jeitos novos de um agendamento acabar sem atendimento, que não são
-- falta nem desistência do paciente:
--   clinica  a clínica cancelou a ida do profissional à cidade ("Cancelar esta passagem"):
--            o paciente fica "a remarcar", com o selo "a clínica desmarcou", e o retorno
--            sem custo dele vale até a próxima passagem pela cidade
--   saiu     o paciente chegou e saiu sem ser atendido
-- A situação continua 'cancelou' (o horário fica livre, como hoje); esta coluna diz por quê.
--
-- Aditiva e idempotente; nenhuma linha existente muda.

alter table public.eventos_agenda
  add column if not exists desmarcou text;

do $$ begin
  if not exists (select 1 from pg_constraint where conname = 'eventos_agenda_desmarcou_check') then
    alter table public.eventos_agenda add constraint eventos_agenda_desmarcou_check
      check (desmarcou is null or desmarcou in ('clinica','saiu'));
  end if;
end $$;

-- rollback:
--   alter table public.eventos_agenda drop constraint if exists eventos_agenda_desmarcou_check,
--     drop column if exists desmarcou;
