-- 490_clinica_resultados.sql
-- CRM da clínica, entrega 1d (docs/mockups/clinica_crm_telas.html, seções 01, 02 e 05,
-- aprovado em 01/10/2026). finance/clinica_pacotes.py e finance/clinica_agenda.py.
--
-- A TERCEIRA PERGUNTA DO FINALIZAR: há resultado a entregar? Biópsia, coleta e exame
-- não fecham o card. Ele espera na coluna Retorno, com a data prevista do laboratório,
-- e só conclui depois da entrega. A recepção marca quando o resultado chegou (e marca
-- a entrega com o paciente) e quando foi entregue.
--
--   estado  aguardando  pedido, o laboratório ainda não devolveu
--           chegou      chegou na clínica: falta entregar ao paciente
--           entregue    entregue: não segura mais o card
--           dispensado  tirado da fila pela recepção
--
-- Um resultado por atendimento (unique evento_id), como o retorno (381). Aditiva e
-- idempotente; tabela nova, nenhuma linha existente muda.

create table if not exists public.clinica_resultados (
  id bigserial primary key,
  conta_id bigint not null references public.contas(id),
  prospeccao_id bigint references public.prospeccao(id) on delete set null,
  evento_id bigint not null unique,        -- o atendimento em que o exame foi colhido
  profissional_id bigint,
  paciente_nome text not null default '',
  paciente_fone text not null default '',
  previsto_em date,                        -- a data que o laboratório deu (pode faltar)
  estado text not null default 'aguardando' check (estado in ('aguardando','chegou','entregue','dispensado')),
  chegou_em timestamptz,
  entregue_em timestamptz,
  criado_em timestamptz not null default now(),
  atualizado_em timestamptz not null default now());
create index if not exists idx_clinica_resultados_conta on public.clinica_resultados (conta_id, estado, previsto_em);

-- rollback:
--   drop table if exists public.clinica_resultados;
