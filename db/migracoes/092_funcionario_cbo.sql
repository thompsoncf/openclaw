-- 092_funcionario_cbo.sql
-- CBO (Classificação Brasileira de Ocupações) do funcionário — aparece no
-- holerite (recibo de pagamento). Opcional: quem não souber deixa em branco.
alter table funcionarios add column if not exists cbo text;
