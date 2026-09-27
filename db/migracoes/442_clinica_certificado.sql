-- 442_clinica_certificado.sql
-- Prontuário, fase 4: CERTIFICADO DIGITAL (docs/mockups/clinica_prontuario.html, seções 04,
-- 11.5, 11.6 e 11.7; seção 15, parte 4). finance/clinica_certificado.py.
--
--   * Cada profissional escolhe no cadastro dele: nenhum (assinatura simples, como hoje),
--     ARQUIVO (A1: o .pfx fica cifrado; a senha nunca é gravada) ou NUVEM (o provedor
--     do certificado, padrão PSC do ITI: o profissional autoriza no aplicativo dele).
--   * A1 liberado: a senha é digitada uma vez por dia. A chave privada fica cifrada com
--     uma chave que só existe na sessão de quem liberou, e vence no fim do dia.
--   * A assinatura ICP-Brasil é uma CAMADA por cima da assinatura simples (fase 2 e 5):
--     o PDF assinado (PAdES AD-RB) fica guardado, cifrado, e nunca muda nem some.
--   * Documento de saúde assinado ganha um código pra farmácia conferir no validador do
--     ITI (QR): só o hash do código fica aqui.
--
-- Aditiva e idempotente.

alter table public.clinica_profissionais
  add column if not exists certificado text not null default 'nenhum',
  add column if not exists certificado_provedor text,
  add column if not exists certificado_desde timestamptz;   -- o que foi assinado antes disso fica simples
do $$ begin
  alter table public.clinica_profissionais add constraint clinica_profissionais_certificado_chk
    check (certificado in ('nenhum','a1','nuvem'));
exception when duplicate_object then null; end $$;

-- o certificado de cada profissional: o arquivo A1 (cifrado) ou o último visto na nuvem
create table if not exists public.clinica_certificados (
  profissional_id bigint primary key references public.clinica_profissionais(id),
  conta_id bigint not null references public.contas(id),
  tipo text not null check (tipo in ('a1','nuvem')),
  arquivo bytea,                       -- A1: o .pfx cifrado (ZQP2), NUNCA a senha
  titular text not null,
  serial text not null,
  emissor text,
  validade timestamptz not null,
  enviado_por text,
  atualizado_em timestamptz not null default now());

-- o A1 liberado no dia: a chave privada cifrada com a chave da sessão de quem liberou
create table if not exists public.clinica_certificado_liberado (
  profissional_id bigint primary key references public.clinica_profissionais(id),
  conta_id bigint not null references public.contas(id),
  membro_id bigint,
  chave bytea not null,
  expira_em timestamptz not null);

-- a assinatura ICP-Brasil de uma evolução ou de um documento
create table if not exists public.clinica_assinaturas_icp (
  id bigserial primary key,
  conta_id bigint not null references public.contas(id),
  cliente_id bigint not null,
  alvo text not null check (alvo in ('evolucao','documento')),
  alvo_id bigint not null,
  profissional_id bigint not null,
  metodo text not null check (metodo in ('a1','nuvem')),
  provedor text,
  titular text not null,
  serial text not null,
  assinado_em timestamptz not null default now(),
  pdf bytea not null,                  -- o PDF assinado (PAdES), cifrado
  pdf_sha256 text not null,
  publico text unique,                 -- o endereço do QR (documento de saúde)
  segredo_sha256 text,                 -- o código impresso: só o hash
  unique (alvo, alvo_id));
create index if not exists clinica_assinaturas_icp_paciente on public.clinica_assinaturas_icp (conta_id, cliente_id);

-- assinado não muda nem some
create or replace function public.clinica_assinatura_icp_nao_muda() returns trigger language plpgsql as $$
begin
  raise exception 'assinatura digital não muda nem se apaga';
end $$;
drop trigger if exists clinica_assinaturas_icp_nao_muda on public.clinica_assinaturas_icp;
create trigger clinica_assinaturas_icp_nao_muda before update or delete on public.clinica_assinaturas_icp
  for each row execute function public.clinica_assinatura_icp_nao_muda();

-- rollback (só se nada foi assinado com certificado):
--   drop table if exists public.clinica_assinaturas_icp, public.clinica_certificado_liberado,
--     public.clinica_certificados;
--   drop function if exists public.clinica_assinatura_icp_nao_muda();
--   alter table public.clinica_profissionais drop constraint if exists clinica_profissionais_certificado_chk,
--     drop column if exists certificado, drop column if exists certificado_provedor,
--     drop column if exists certificado_desde;
