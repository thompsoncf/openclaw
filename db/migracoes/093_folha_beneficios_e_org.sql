-- 093_folha_beneficios_e_org.sql
-- 1) Benefícios na folha (vale-refeição/alimentação): custo do empregador que
--    NÃO desconta do funcionário. Novo tipo 'beneficio' em folha_eventos.
-- 2) Estrutura organizacional do funcionário (aparece no holerite):
--    departamento (default GERAL), setor e seção.

-- tipo 'beneficio' — o check é recriado (o nome é estável desde a 053).
alter table folha_eventos drop constraint if exists folha_eventos_tipo_check;
alter table folha_eventos add constraint folha_eventos_tipo_check
    check (tipo in ('vale','extra','desconto','pagamento','beneficio'));

alter table funcionarios add column if not exists departamento text;
alter table funcionarios add column if not exists setor        text;
alter table funcionarios add column if not exists secao        text;
