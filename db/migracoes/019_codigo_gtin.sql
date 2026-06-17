-- Adiciona suporte a EAN/código de barras em itens e precos observados
ALTER TABLE itens_lancamento  ADD COLUMN IF NOT EXISTS codigo text;
ALTER TABLE precos_observados ADD COLUMN IF NOT EXISTS gtin   text;
CREATE INDEX IF NOT EXISTS ix_precos_obs_gtin
  ON precos_observados(gtin) WHERE gtin IS NOT NULL;
