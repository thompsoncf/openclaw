-- MIGRACAO 015: indice pra agregacao de precos por produto
-- Quando agregamos por descricao_norm com loja_id, precisamos de um indice composto
-- pra nao fazer table scan em 50k+ linhas.
create index if not exists idx_precos_agg on precos_observados(descricao_norm, loja_id, valor_unitario_centavos);
