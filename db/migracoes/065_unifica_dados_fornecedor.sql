-- 065_unifica_dados_fornecedor.sql
-- UNIFICA os dados cadastrais: contas passa a ser a fonte unica de
-- razao_social/documento(cnpj)/endereco. A tabela fornecedor_fiscal guardava
-- esses campos DUPLICADOS — agora eles vivem so em contas. O unico campo
-- exclusivo do fiscal (inscricao_estadual) desce pra contas tambem.
--
-- Isso resolve o bug do gate: dados_empresa_completos olha contas, e o
-- fornecedor ja cadastrado passa a ter contas.documento/razao_social preenchidos.
-- Idempotente. NAO derruba fornecedor_fiscal ainda (a tela para de usa'-la; a
-- tabela pode ser removida numa limpeza futura, sem pressa).

-- 1) campo fiscal que faltava em contas
alter table contas add column if not exists inscricao_estadual text;

-- 2) BACKFILL: copia os dados de quem ja e' fornecedor pra contas, SO quando a
--    conta ainda nao tem o dado (nao sobrescreve o que a Empresa ja preencheu).
update contas ct
   set documento     = coalesce(nullif(ct.documento, ''), ff.cnpj),
       razao_social  = coalesce(nullif(ct.razao_social, ''), ff.razao_social),
       endereco      = coalesce(nullif(ct.endereco, ''), ff.endereco),
       inscricao_estadual = coalesce(ct.inscricao_estadual, ff.inscricao_estadual)
  from fornecedor_fiscal ff
 where ff.conta_id = ct.id;

-- rollback:
--   alter table contas drop column if exists inscricao_estadual;
--   (o backfill nao e' revertido — os dados ja pertencem a contas)
