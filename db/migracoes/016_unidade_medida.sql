-- 016: unidade de medida (UN/KG/L) nos itens e no banco de precos.
-- Permite comparar "maca com maca": so' comparar precos da MESMA unidade.
-- Default 'UN' (a maioria dos itens e' por unidade).

alter table itens_lancamento  add column if not exists unidade text not null default 'UN';
alter table precos_observados add column if not exists unidade text not null default 'UN';
