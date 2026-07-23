-- 089_funcionario_vale_transporte.sql
-- Vale-transporte por funcionário: quem opta tem 6% do salário descontado do
-- líquido da folha (limite legal do empregado). Coluna nova, default false ->
-- ninguém é afetado até marcar. O desconto de INSS (progressivo) é calculado em
-- tempo real a partir do salário, não precisa de coluna.

alter table funcionarios
    add column if not exists vale_transporte boolean not null default false;
