-- A foto, o vídeo e o documento que o cliente manda param de ser descartados.
--
-- O ARQUIVO NÃO ENTRA AQUI. O WhatsApp já guarda a mídia cifrada no CDN dele, e a
-- mensagem que chega traz o endereço (`directPath`) e a chave que decifra
-- (`mediaKey`). São ~200 bytes. Guardar o arquivo seriam ~110 GB por ano só na
-- Prime, contra 22 MB de ponteiro — e ainda pediria política de retenção, conta de
-- disco e limpeza. Quem busca e decifra é o serviço web, sob demanda, em stream.
--
-- Medido em 28/08/2026: 598 mensagens de um-para-um sem texto descartadas na Prime
-- em 48h (299 por dia), mais 118 no Rawilson. Eram 124/dia uma semana antes — o
-- buraco cresce junto com o uso.
alter table public.mensagens
  -- {directPath, mediaKey, mimetype} — o ponteiro pro CDN. jsonb porque o formato
  -- é do WhatsApp e pode ganhar campo sem migração nova.
  add column if not exists midia_ref jsonb,
  -- imagem | video | documento | figurinha. Decide o desenho da bolha E a string
  -- de HKDF que deriva a chave ("WhatsApp Image Keys" etc).
  add column if not exists midia_tipo text,
  -- nome do arquivo, bytes, duração, dimensões: o que a bolha mostra ANTES de
  -- carregar, e o que sobra quando o CDN já apagou o arquivo.
  add column if not exists midia_meta jsonb;

-- Só as linhas com mídia. Índice parcial porque a esmagadora maioria das mensagens
-- é texto puro e não tem por que ocupar espaço aqui.
create index if not exists idx_mensagens_midia
  on public.mensagens (conversa_id, criado_em desc)
  where midia_ref is not null;
