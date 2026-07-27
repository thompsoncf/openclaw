-- 097_whatsapp_qr.sql
-- Sessão do WhatsApp via QR Code (tipo WhatsApp Web / Baileys), operada por um
-- serviço Node à parte (services/wa-qr). O estado de autenticação (creds + chaves
-- de sinal) é guardado AQUI no Postgres pra sobreviver a restart sem disco
-- persistente no Render — o serviço Node lê/escreve estas linhas. Idempotente.
--
-- provedor='qr' em canais_config marca a empresa que usa esta via (087→096 já
-- criaram a coluna provedor). Aqui só adicionamos a tabela do estado da sessão.

create table if not exists public.wa_qr_auth (
  conta_id     bigint not null references public.contas(id) on delete cascade,
  arquivo      text   not null,               -- 'creds' | 'app-state-sync-key-<id>' | 'session-<id>' ...
  conteudo     text   not null,               -- JSON (BufferJSON) serializado pelo Baileys
  atualizado   timestamptz not null default now(),
  primary key (conta_id, arquivo)
);
