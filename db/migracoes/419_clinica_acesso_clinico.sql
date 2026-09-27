-- 419_clinica_acesso_clinico.sql
-- Prontuário, fase 1: QUEM VÊ O QUÊ (docs/mockups/clinica_prontuario.html, seção 01 e
-- seção 15, parte 1). finance/clinica_acesso_clinico.py.
--
--   * O conteúdo clínico (pré-consulta; depois evolução, fotos, documentos) só abre pra
--     PROFISSIONAL DE SAÚDE da clínica: o cadastro dele em Configurar › Profissionais,
--     com conselho, o login ligado e `acesso_clinico` — que SÓ O DONO liga (o gestor não
--     se dá acesso digitando um conselho). Trocar o login ou o conselho desliga de novo.
--   * `acesso_clinico_membro_id`: a liberação vale pro LOGIN que estava ligado quando o
--     dono liberou. Trocar o login do cadastro (o gestor ligando o dele) não herda nada.
--   * `e_dono`: "este profissional é o dono da conta" (o médico que é dono entra com o
--     login do dono, que não é membro da equipe). Um por conta.
--   * `clinica_acessos`: o REGISTRO DE ACESSO — quem abriu o conteúdo clínico de qual
--     paciente, quando e o quê (nunca o conteúdo). Só insere: não se altera nem se apaga
--     (trigger). Sem FK pra ficha: o registro não some com ela.
--
-- Aditiva e idempotente.

alter table public.clinica_profissionais
  add column if not exists acesso_clinico boolean not null default false,
  add column if not exists acesso_clinico_em timestamptz,
  add column if not exists acesso_clinico_membro_id bigint,
  add column if not exists e_dono boolean not null default false;
create unique index if not exists ux_clinica_profissionais_dono
  on public.clinica_profissionais (conta_id) where e_dono and ativo;

create table if not exists public.clinica_acessos (
  id bigserial primary key,
  conta_id bigint not null references public.contas(id),
  cliente_id bigint,                          -- sem FK: o registro fica mesmo se a ficha sair
  profissional_id bigint,
  membro_id bigint,
  quem text not null,                         -- o nome de quem abriu, como estava na hora
  o_que text not null,                        -- 'pré-consulta', 'liberou o prontuário'…
  ip text,
  criado_em timestamptz not null default now());
create index if not exists clinica_acessos_conta_quando on public.clinica_acessos (conta_id, criado_em desc);
create index if not exists clinica_acessos_conta_paciente
  on public.clinica_acessos (conta_id, cliente_id, criado_em desc) where cliente_id is not null;

create or replace function public.clinica_acessos_so_insere() returns trigger language plpgsql as $$
begin
  raise exception 'o registro de acesso ao prontuário não se altera nem se apaga';
end $$;
drop trigger if exists clinica_acessos_so_insere on public.clinica_acessos;
create trigger clinica_acessos_so_insere before update or delete on public.clinica_acessos
  for each row execute function public.clinica_acessos_so_insere();

-- rollback:
--   drop trigger if exists clinica_acessos_so_insere on public.clinica_acessos;
--   drop function if exists public.clinica_acessos_so_insere();
--   drop table if exists public.clinica_acessos;
--   drop index if exists ux_clinica_profissionais_dono;
--   alter table public.clinica_profissionais drop column if exists e_dono,
--     drop column if exists acesso_clinico_membro_id,
--     drop column if exists acesso_clinico_em, drop column if exists acesso_clinico;
