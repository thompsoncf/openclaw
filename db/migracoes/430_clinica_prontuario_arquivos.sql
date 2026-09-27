-- 430_clinica_prontuario_arquivos.sql
-- Prontuário, fase 3: FOTOS CLÍNICAS E ANEXOS (docs/mockups/clinica_prontuario.html,
-- seção 05; seção 15, parte 3). finance/clinica_prontuario_arquivos.py.
--
--   * O arquivo vai pro bucket PRIVADO do Storage já CIFRADO pelo Zaq (AES-256-GCM, a
--     chave PRONTUARIO_CHAVE só no servidor): nem quem tem o bucket vê a foto. O banco
--     guarda o caminho, o tipo, o tamanho e a impressão digital (sha256) do original.
--   * Foto sem metadados (GPS, aparelho): o Zaq regrava a imagem antes de cifrar.
--   * Ver passa pelo portão do acesso clínico (fase 1): só o profissional liberado, e
--     cada abertura vai pro registro de acesso. O endereço é o da tela (com sessão),
--     nunca um link público.
--   * Não se apaga nem se muda (triggers): a guarda é de 20 anos, como o prontuário.
--
-- Aditiva e idempotente.

create table if not exists public.clinica_prontuario_arquivos (
  id bigserial primary key,
  conta_id bigint not null references public.contas(id),
  cliente_id bigint not null,
  evento_id bigint,
  profissional_id bigint not null,
  tipo text not null check (tipo in ('foto','anexo')),
  regiao text not null default '',
  legenda text not null default '',
  mimetype text not null,
  tamanho integer not null,
  sha256 text not null,
  caminho text not null,
  autorizacao text not null default '',        -- o termo de imagem que valia (ou o papel)
  criado_em timestamptz not null default now());
create index if not exists clinica_prontuario_arquivos_paciente
  on public.clinica_prontuario_arquivos (conta_id, cliente_id, criado_em desc);

drop trigger if exists clinica_prontuario_arquivos_nao_apaga on public.clinica_prontuario_arquivos;
create trigger clinica_prontuario_arquivos_nao_apaga before delete or update on public.clinica_prontuario_arquivos
  for each row execute function public.clinica_prontuario_nao_apaga();

-- rollback (só se nada foi guardado):
--   drop table if exists public.clinica_prontuario_arquivos;
