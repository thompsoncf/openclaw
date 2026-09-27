-- 423_clinica_prontuario.sql
-- Prontuário, fase 2: a FICHA CLÍNICA e a EVOLUÇÃO (docs/mockups/clinica_prontuario.html,
-- seções 02, 03 e 04; seção 15, parte 2). finance/clinica_prontuario.py.
--
--   * `clinica_ficha_clinica`: alergias, medicamentos em uso, problemas e antecedentes.
--     Cada salvamento é uma linha nova (a atual é a última): quem mudou o quê, quando.
--   * `clinica_evolucoes`: a evolução de cada atendimento, pelo modelo (dermatologia,
--     fisioterapia dermatofuncional, procedimento, texto livre). Rascunho: só o autor vê
--     e edita. Assinada: hora do servidor, profissional, conselho e a impressão digital
--     do conteúdo (sha256) — vira imutável. Correção só por ADENDO (`adendo_de`), também
--     assinado.
--   * APAGAR NÃO EXISTE (a lei manda guardar 20 anos): triggers recusam apagar evolução
--     ASSINADA e versão da ficha, e mudar evolução assinada ou versão da ficha. O rascunho
--     (ainda não é prontuário) o autor pode descartar. Sem FK pra ficha do paciente: o
--     prontuário não some com ela.
--   * Só o profissional liberado lê e escreve (finance/clinica_acesso_clinico, fase 1);
--     toda abertura vai pro registro de acesso. O agente do WhatsApp nunca lê.
--
-- Aditiva e idempotente.

create table if not exists public.clinica_ficha_clinica (
  id bigserial primary key,
  conta_id bigint not null references public.contas(id),
  cliente_id bigint not null,
  alergias text not null default '',
  medicamentos text not null default '',
  problemas text not null default '',
  profissional_id bigint,
  profissional_nome text not null default '',
  criado_em timestamptz not null default now());
create index if not exists clinica_ficha_clinica_paciente
  on public.clinica_ficha_clinica (conta_id, cliente_id, criado_em desc);

create table if not exists public.clinica_evolucoes (
  id bigserial primary key,
  conta_id bigint not null references public.contas(id),
  cliente_id bigint not null,
  evento_id bigint,
  profissional_id bigint not null,
  modelo text not null check (modelo in ('dermatologia','fisio','procedimento','livre','adendo')),
  campos jsonb not null default '{}'::jsonb,
  cid text,
  retorno_dias integer check (retorno_dias is null or retorno_dias between 1 and 730),
  adendo_de bigint references public.clinica_evolucoes(id),
  status text not null default 'rascunho' check (status in ('rascunho','assinado')),
  assinado_em timestamptz,
  assinatura_hash text,
  profissional_nome text,                     -- como estava na hora de assinar
  conselho text,
  criado_em timestamptz not null default now(),
  atualizado_em timestamptz not null default now());
create index if not exists clinica_evolucoes_paciente on public.clinica_evolucoes (conta_id, cliente_id, criado_em desc);
create index if not exists clinica_evolucoes_evento on public.clinica_evolucoes (conta_id, evento_id) where evento_id is not null;

create or replace function public.clinica_prontuario_nao_apaga() returns trigger language plpgsql as $$
begin
  raise exception 'o prontuário não se apaga (guarda de 20 anos)';
end $$;
create or replace function public.clinica_evolucao_assinada_nao_apaga() returns trigger language plpgsql as $$
begin
  if old.status = 'assinado' then
    raise exception 'o prontuário não se apaga (guarda de 20 anos)';
  end if;
  return old;
end $$;
create or replace function public.clinica_evolucao_assinada_nao_muda() returns trigger language plpgsql as $$
begin
  if old.status = 'assinado' then
    raise exception 'evolução assinada não muda: a correção é um adendo';
  end if;
  return new;
end $$;
create or replace function public.clinica_ficha_clinica_nao_muda() returns trigger language plpgsql as $$
begin
  raise exception 'a ficha clínica não se edita por cima: cada mudança é uma versão nova';
end $$;

drop trigger if exists clinica_evolucoes_nao_apaga on public.clinica_evolucoes;
create trigger clinica_evolucoes_nao_apaga before delete on public.clinica_evolucoes
  for each row execute function public.clinica_evolucao_assinada_nao_apaga();
create unique index if not exists ux_clinica_evolucoes_rascunho_do_evento
  on public.clinica_evolucoes (conta_id, evento_id, profissional_id)
  where status = 'rascunho' and adendo_de is null and evento_id is not null;
drop trigger if exists clinica_evolucoes_assinada on public.clinica_evolucoes;
create trigger clinica_evolucoes_assinada before update on public.clinica_evolucoes
  for each row execute function public.clinica_evolucao_assinada_nao_muda();
drop trigger if exists clinica_ficha_clinica_nao_apaga on public.clinica_ficha_clinica;
create trigger clinica_ficha_clinica_nao_apaga before delete on public.clinica_ficha_clinica
  for each row execute function public.clinica_prontuario_nao_apaga();
drop trigger if exists clinica_ficha_clinica_nao_muda on public.clinica_ficha_clinica;
create trigger clinica_ficha_clinica_nao_muda before update on public.clinica_ficha_clinica
  for each row execute function public.clinica_ficha_clinica_nao_muda();

-- rollback (só se nada foi escrito — o prontuário não se apaga):
--   drop table if exists public.clinica_evolucoes; drop table if exists public.clinica_ficha_clinica;
--   drop function if exists public.clinica_prontuario_nao_apaga(), public.clinica_evolucao_assinada_nao_muda(),
--     public.clinica_evolucao_assinada_nao_apaga(),
--     public.clinica_ficha_clinica_nao_muda();
