-- 471_clinica_tratamento_proposto.sql
-- CRM da clínica, entrega 1a (docs/mockups/clinica_crm_telas.html, seções 01 e 02,
-- aprovado em 01/10/2026). finance/clinica_agenda.py.
--
-- A RESPOSTA DO FINALIZAR FICA NO AGENDAMENTO. "O médico propôs tratamento?" só movia
-- o card e se perdia. Com a coluna Consulta guardando o paciente que veio E o paciente
-- que espera o plano ("plano a montar"), o segundo Finalizar do mesmo card (a mãe e o
-- filho no mesmo celular, a volta pra mostrar um exame) fechava como venda, com o valor
-- proposto, um card que ainda ia receber o plano. Guardada no agendamento, a resposta
-- diz se há plano a montar, e depois quantas consultas viram proposta.
--
--   true  = propôs    false = não propôs    null = não perguntado (sessão de pacote,
--   agendamento antigo, compromisso que não é da clínica)
--
-- Aditiva e idempotente. Nenhuma linha existente muda.

alter table public.eventos_agenda
  add column if not exists tratamento_proposto boolean;

comment on column public.eventos_agenda.tratamento_proposto is
  'Clínica: a resposta do Finalizar a "o médico propôs tratamento?". Null = não perguntado.';

-- rollback:
--   alter table public.eventos_agenda drop column if exists tratamento_proposto;
