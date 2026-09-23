-- 317_titulo_classificacao.sql
-- A conta a pagar/receber passa a guardar PLANO DE CONTAS e CENTRO DE CUSTO — e
-- a baixa os leva pro livro-caixa.
--
-- POR QUE. Pedido do dono em 23/09/2026: "no lançamento do contas a pagar já
-- colocar o centro de custo e plano de contas e categoria". Medido na Prime
-- (conta 34) no mesmo dia: das 20 despesas de setembro que nasceram de conta a
-- pagar, 20 tinham plano de contas — alguém classificou cada uma À MÃO, depois,
-- na lista do Financeiro, cobrado pelo aviso "Lançamentos a classificar" — e só
-- 1 tinha centro de custo, que não tem aviso nenhum cobrando.
--
-- A porta do extrato pergunta as duas coisas; a do título não perguntava nada.
-- As colunas vêm pro TÍTULO (e não só pro lançamento) porque é no título que a
-- conta nasce — o aluguel é cadastrado dias antes de ser pago, e é ali que quem
-- lança sabe o que ela é. A baixa copia pro lançamento.
--
-- NÃO MEXE EM CENTRO DE CUSTO NENHUM. Regra do dono em 23/09/2026 ("não mexer em
-- centro de custos"): esta migração não cria, não renomeia e não altera centro
-- algum. Só dá ao título um lugar pra apontar pros que já existem.
--
-- `on delete set null` pelo mesmo motivo da 132: desligar uma conta do plano ou
-- desativar um centro não pode derrubar título. (Centro na prática nunca é
-- apagado — `desativar_centro` só liga o `ativo`.)
--
-- Aditiva e idempotente. Título antigo fica com os dois nulos, que é exatamente o
-- que ele sempre foi.

alter table public.titulos
    add column if not exists plano_conta_id  bigint
        references public.plano_contas(id)  on delete set null,
    add column if not exists centro_custo_id bigint
        references public.centros_custo(id) on delete set null;

comment on column public.titulos.plano_conta_id is
  'Conta do plano (global) que classifica este título. A baixa copia pro '
  'lançamento. Nulo = não classificado — ver a migração 317.';
comment on column public.titulos.centro_custo_id is
  'Centro de custo DESTA conta que classifica o título. A baixa copia pro '
  'lançamento; a conciliação só preenche se o lançamento não tiver.';

-- rollback:
--   alter table public.titulos drop column if exists centro_custo_id,
--                              drop column if exists plano_conta_id;
