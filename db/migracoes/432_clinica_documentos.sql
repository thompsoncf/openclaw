-- 432_clinica_documentos.sql
-- Prontuário, fase 5: DOCUMENTOS (docs/mockups/clinica_prontuario.html, seção 06 e 11.7;
-- seção 15, parte 5). finance/clinica_documentos.py.
--
--   * Receita, receita de controle especial (2 vias), atestado, declaração de
--     comparecimento, declaração de acompanhante, pedido de exame, laudo e orientações:
--     o profissional escreve (rascunho) e EMITE — vira imutável, com hora do servidor,
--     conselho e a impressão digital do conteúdo. O PDF sai com o cabeçalho da clínica.
--   * Notificação de receita (amarela/azul): continua no talão de papel; aqui só fica o
--     registro de que foi emitida e o número.
--   * Sem certificado digital (fase 4), o PDF diz pra imprimir e assinar à mão.
--   * ENVIO: nunca o arquivo solto na conversa — vai o link da ficha (abre com a data de
--     nascimento), e o documento fica visível lá por 30 dias depois de enviado. O agente
--     do WhatsApp nunca envia documento.
--   * Emitido não muda nem some (as mesmas travas da evolução, migração 423).
--
-- Aditiva e idempotente.

create table if not exists public.clinica_documentos (
  id bigserial primary key,
  conta_id bigint not null references public.contas(id),
  cliente_id bigint not null,
  evento_id bigint,
  profissional_id bigint not null,
  tipo text not null check (tipo in ('receita','receita_controle','notificacao','atestado','comparecimento',
                                     'acompanhante','pedido_exame','laudo','orientacoes')),
  titulo text not null,
  corpo text not null default '',
  numero_talao text,
  status text not null default 'rascunho' check (status in ('rascunho','assinado')),
  assinado_em timestamptz,
  assinatura_hash text,
  profissional_nome text,
  conselho text,
  enviado_em timestamptz,
  criado_em timestamptz not null default now(),
  atualizado_em timestamptz not null default now());
create index if not exists clinica_documentos_paciente on public.clinica_documentos (conta_id, cliente_id, criado_em desc);

-- o envio (enviado_em) é a ÚNICA coisa que muda num documento emitido
create or replace function public.clinica_documento_emitido_nao_muda() returns trigger language plpgsql as $$
begin
  if old.status = 'assinado' and (new.corpo is distinct from old.corpo or new.titulo is distinct from old.titulo
      or new.tipo is distinct from old.tipo or new.status is distinct from old.status
      or new.assinado_em is distinct from old.assinado_em or new.assinatura_hash is distinct from old.assinatura_hash
      or new.numero_talao is distinct from old.numero_talao or new.cliente_id is distinct from old.cliente_id
      or new.profissional_id is distinct from old.profissional_id or new.conselho is distinct from old.conselho
      or new.profissional_nome is distinct from old.profissional_nome) then
    raise exception 'documento emitido não muda: emita outro';
  end if;
  return new;
end $$;
drop trigger if exists clinica_documentos_nao_muda on public.clinica_documentos;
create trigger clinica_documentos_nao_muda before update on public.clinica_documentos
  for each row execute function public.clinica_documento_emitido_nao_muda();
drop trigger if exists clinica_documentos_nao_apaga on public.clinica_documentos;
create trigger clinica_documentos_nao_apaga before delete on public.clinica_documentos
  for each row execute function public.clinica_evolucao_assinada_nao_apaga();

-- rollback (só se nada foi emitido):
--   drop table if exists public.clinica_documentos;
--   drop function if exists public.clinica_documento_emitido_nao_muda();
