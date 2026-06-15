-- MIGRACAO 014: adiciona ramo + cnae a lojas (classificacao por departamento)
-- Ramo: FarmÃ¡cia, Mercado, Pet, etc - derivado do CNAE da Receita ou categoria do Claude
-- CNAE: descricao fiscal da atividade (consulta BrasilAPI)
alter table lojas add column if not exists ramo text;
alter table lojas add column if not exists cnae text;
create index if not exists idx_lojas_ramo on lojas(ramo);
