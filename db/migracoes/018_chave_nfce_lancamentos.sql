-- 018: trava de duplicidade por Chave de Acesso NFC-e
-- O mesmo cupom fiscal nunca entra 2x na mesma conta (chave é única por documento)

ALTER TABLE lancamentos ADD COLUMN IF NOT EXISTS chave varchar(44);

-- Índice único per-conta: mesma chave não pode entrar 2x na mesma conta.
-- Parcial: multiplos NULL são permitidos (cupons sem chave).
CREATE UNIQUE INDEX IF NOT EXISTS ux_lanc_chave_conta
  ON lancamentos (conta_id, chave) WHERE chave IS NOT NULL;
