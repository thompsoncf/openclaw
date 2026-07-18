-- 069_orcamento_cnpj.sql
-- Captacao inteligente (camada 1): guarda o CNPJ do cliente no orcamento.
-- A tela passa a consultar a BrasilAPI/Receita pelo CNPJ e preencher a ficha;
-- aqui so' persistimos o CNPJ. add-if-not-exists: idempotente, zero impacto no ar.
alter table orcamentos add column if not exists cnpj text;
