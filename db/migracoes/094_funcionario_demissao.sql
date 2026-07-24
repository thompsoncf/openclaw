-- 094_funcionario_demissao.sql
-- Data de demissão do funcionário. Ao preencher, ele sai da folha a partir do
-- mês SEGUINTE (no mês da demissão ainda aparece pra acertar); depois fica só no
-- histórico/relatório. Rescisão (acertos finais) segue com o contador.
alter table funcionarios add column if not exists demitido_em date;
