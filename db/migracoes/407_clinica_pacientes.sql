-- 407_clinica_pacientes.sql
-- Clínica: o PACIENTE separado do card do WhatsApp, a lista e a ficha de pacientes
-- (docs/mockups/clinica_prontuario.html, seções 11.1, 11.2 e 13 — aprovado pelo dono
-- em 26/09/2026). finance/clinica_pacientes.py, tela /painel/clinica/pacientes.
--
--   * O paciente mora na ficha de clientes do Zaq (`clientes` + a identidade em
--     `pessoas`, com o CPF). O que a clínica acrescenta:
--       - `prospeccao_id`: o card do WhatsApp por onde ele chegou. UM número pode ter
--         VÁRIOS pacientes (a mãe e os filhos), cada um com a sua ficha;
--       - `responsavel_id`: o responsável de quem é menor (ele recebe as mensagens,
--         assina os termos e recebe a nota);
--       - `como_conheceu`: a origem (Instagram, indicação, anúncio…).
--   * O AGENDAMENTO passa a apontar pro paciente (`eventos_agenda.cliente_id`). A
--     conta da clínica tinha 0 agendamentos no Zaq em 26/09/2026: a ligação nasce
--     certa, sem converter nada antigo.
--
-- Aditiva e idempotente.

alter table public.clientes
  add column if not exists prospeccao_id bigint references public.prospeccao(id) on delete set null,
  add column if not exists responsavel_id bigint references public.clientes(id) on delete set null,
  add column if not exists como_conheceu text;
create index if not exists clientes_dono_prospeccao on public.clientes (dono_id, prospeccao_id)
  where prospeccao_id is not null;

alter table public.eventos_agenda
  add column if not exists cliente_id bigint references public.clientes(id) on delete set null;
create index if not exists eventos_agenda_conta_cliente on public.eventos_agenda (conta_id, cliente_id)
  where cliente_id is not null;

-- rollback:
--   drop index if exists eventos_agenda_conta_cliente; alter table public.eventos_agenda drop column if exists cliente_id;
--   drop index if exists clientes_dono_prospeccao;
--   alter table public.clientes drop column if exists como_conheceu, drop column if exists responsavel_id,
--     drop column if exists prospeccao_id;
