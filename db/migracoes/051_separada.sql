alter table cesta_semana add column if not exists separada_em timestamptz;
alter table carrinhos   add column if not exists separada_em timestamptz;
