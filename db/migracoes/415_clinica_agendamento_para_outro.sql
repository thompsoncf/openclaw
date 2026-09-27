-- 415_clinica_agendamento_para_outro.sql
-- Clínica, passo 0c-2: a consulta marcada pra OUTRA pessoa do mesmo WhatsApp (a mãe que
-- marca pro filho). finance/clinica_pacientes.completar_do_agendamento grava o sinal;
-- finance/clinica_agenda.quem_recebe usa: a confirmação e a véspera cumprimentam quem
-- recebe ("Lívia, a consulta de Pedro"), nunca "Pedro, sua consulta" no celular da mãe.
--
-- O sinal é EXPLÍCITO (o agente perguntou "é pra você ou pra outra pessoa?", a recepção
-- marcou a caixa, a mãe disse "era pro meu filho"), nunca deduzido comparando nomes: o
-- perfil "Duda 💕" é a própria Maria Eduarda.
--
-- Aditiva e idempotente.

alter table public.eventos_agenda add column if not exists para_outro boolean not null default false;

-- rollback:
--   alter table public.eventos_agenda drop column if exists para_outro;
