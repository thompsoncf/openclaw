-- 095_funcionario_cpf.sql
-- CPF do funcionário — aparece no recibo de pagamento (padrão do escritório).
-- Opcional; guardamos só os dígitos e formatamos na exibição.
alter table funcionarios add column if not exists cpf text;
