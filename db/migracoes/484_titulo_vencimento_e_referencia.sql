-- 484_titulo_vencimento_e_referencia.sql
-- Contas a pagar: mudar o VENCIMENTO depois de lançada e anotar o MÊS DE
-- REFERÊNCIA — pedido do dono em 02/10/2026.
--
-- mes_referencia     o mês a que a conta se refere (1º dia do mês). SÓ
--                    INFORMAÇÃO (decisão do dono): aparece na lista, na edição e
--                    no relatório; o DRE continua pela data do pagamento.
--                    NULL = o sistema assume o MÊS ANTERIOR AO VENCIMENTO (decisão
--                    do dono: a conta que vence 10/11 é a de outubro). É calculado
--                    na leitura, e não gravado aqui, de propósito: nenhuma das
--                    contas que já existem é reescrita.
-- vencimento_manual  o dono mudou o vencimento à mão. A folha em dois dias (482)
--                    move a data das contas dela quando a regra muda; a data que o
--                    dono escolheu ela respeita.
--
-- Aditiva e idempotente; nada existente muda de valor.

alter table public.titulos
  add column if not exists mes_referencia date
    check (mes_referencia is null or extract(day from mes_referencia) = 1),
  add column if not exists vencimento_manual boolean not null default false;

-- rollback (manual):
--   alter table public.titulos drop column if exists vencimento_manual,
--     drop column if exists mes_referencia;
