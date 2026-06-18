-- Camada 2 Parte 2: agregar precos por GTIN (produto canônico, não nome)
-- Coluna gtin em precos_vigentes, constraint unique refatorado pra coalesce(gtin, descricao_norm)

ALTER TABLE precos_vigentes ADD COLUMN IF NOT EXISTS gtin text;

-- Derrubar a constraint unique antiga (em descricao_norm)
-- pg_constraint busca dinamicamente; queremos a constraint que manda em descricao_norm
DO $$
DECLARE c text;
BEGIN
  SELECT conname INTO c FROM pg_constraint
   WHERE conrelid = 'precos_vigentes'::regclass AND contype = 'u'
     AND pg_get_constraintdef(oid) LIKE '%descricao_norm%'
   LIMIT 1;
  IF c IS NOT NULL THEN
    EXECUTE 'ALTER TABLE precos_vigentes DROP CONSTRAINT ' || quote_ident(c);
  END IF;
END $$;

-- Nova unicidade: por PRODUTO (gtin se existe, senao descricao_norm) + loja + regiao + unidade
-- Assim dois produtos do mesmo GTIN em lojas/regioes diferentes ficam em linhas separadas,
-- mas o MESMO GTIN com nome diferente (roso vs roxo) fica junto (agregado).
CREATE UNIQUE INDEX IF NOT EXISTS ux_vigentes_produto_gtin
  ON precos_vigentes (coalesce(gtin, descricao_norm), coalesce(loja_id, 0), regiao, unidade);
