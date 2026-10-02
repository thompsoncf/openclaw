-- 482_folha_adiantamento_e_saldo.sql
-- A folha em DOIS dias: adiantamento (ex.: dia 20) e saldo (5º dia útil do mês
-- seguinte), virando contas a pagar sozinhas — pedido do dono em 02/10/2026.
--
-- FUNCIONÁRIO: a empresa escolhe por pessoa (decisão do dono).
--   adiantamento_dia       dia do mês do adiantamento; NULL = sem adiantamento
--   adiantamento_pct       % do salário vigente na competência
--   adiantamento_centavos  ou um valor fixo — vale o que estiver preenchido
--   saldo_regra            'dia' (o dia_pagamento de sempre, no mês seguinte) ou
--                          'quinto_util' (5º dia útil do mês seguinte, o prazo da CLT)
--   titulos_folha_desde    a partir de qual competência as contas a pagar nascem;
--                          NULL = desligado. É data, e não liga/desliga, porque
--                          ligar hoje não pode inventar conta de um mês que a
--                          empresa já pagou por fora.
--
-- TÍTULO: o vínculo com a folha. Na baixa, o pagamento vira o evento da folha
-- (adiantamento ou pagamento) sem lançar o caixa duas vezes.
--   folha_valor_calculado  o valor que a folha calculou. Se o dono mudar o valor
--                          da conta à mão, os dois divergem — e a sincronização
--                          respeita a mão dele em vez de desfazer.
--
-- Aditiva e idempotente; nada existente muda de valor (titulos_folha_desde nasce
-- NULL pra todo mundo).

alter table public.funcionarios
  add column if not exists adiantamento_dia smallint
    check (adiantamento_dia between 1 and 28),
  add column if not exists adiantamento_pct numeric(5,2)
    check (adiantamento_pct > 0 and adiantamento_pct < 100),
  add column if not exists adiantamento_centavos int
    check (adiantamento_centavos > 0),
  add column if not exists saldo_regra text not null default 'dia'
    check (saldo_regra in ('dia','quinto_util')),
  add column if not exists titulos_folha_desde date;

alter table public.titulos
  add column if not exists folha_funcionario_id bigint
    references public.funcionarios(id) on delete set null,
  add column if not exists folha_competencia date,
  add column if not exists folha_parte text
    check (folha_parte in ('adiantamento','saldo')),
  add column if not exists folha_valor_calculado int;

-- UMA conta de cada parte por pessoa e mês: é o que deixa a sincronização rodar
-- todo dia sem duplicar. Cancelada não conta (pode nascer de novo quando o dono
-- muda a configuração).
create unique index if not exists ux_titulos_folha
  on public.titulos (conta_id, folha_funcionario_id, folha_competencia, folha_parte)
  where folha_parte is not null and status <> 'cancelado';

-- rollback (manual):
--   drop index if exists public.ux_titulos_folha;
--   alter table public.titulos drop column if exists folha_valor_calculado,
--     drop column if exists folha_parte, drop column if exists folha_competencia,
--     drop column if exists folha_funcionario_id;
--   alter table public.funcionarios drop column if exists titulos_folha_desde,
--     drop column if exists saldo_regra, drop column if exists adiantamento_centavos,
--     drop column if exists adiantamento_pct, drop column if exists adiantamento_dia;
