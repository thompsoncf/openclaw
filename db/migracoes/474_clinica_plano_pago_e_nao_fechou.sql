-- 474_clinica_plano_pago_e_nao_fechou.sql
-- CRM da clínica, entrega 1b (docs/mockups/clinica_crm_telas.html, seção 01, "O dinheiro",
-- aprovado em 01/10/2026). finance/clinica_planos.py.
--
-- PLANO ACEITO NÃO É PLANO PAGO. Na conta com o funil novo, o aceite deixa o card em
-- "Plano ou orçamento enviado", aceito e aguardando o pagamento; o card só vai pra
-- Em tratamento quando o pagamento ou a entrada entram (uma parcela do plano paga).
--   pago_em            quando o Zaq viu a primeira parcela do plano paga (a recepção
--                      clicou Recebi, ou a baixa veio do financeiro). Null = a pagar.
--
-- PLANO QUE NÃO FECHA NÃO PERDE O PACIENTE. Recusado (pelo link ou na recepção) ou
-- vencido, o plano guarda o motivo e o card vai pra Retorno ou Concluído.
--   nao_fechou_motivo  o motivo em texto: "Achou caro", "recusou pelo link",
--                      "venceu sem resposta"...
--
-- Aditiva e idempotente. Nenhuma linha existente muda (conferido em 01/10/2026: a conta
-- 39, a única do nicho, ainda não tem plano gravado).

alter table public.clinica_planos
  add column if not exists pago_em timestamptz,
  add column if not exists nao_fechou_motivo text;

-- rollback:
--   alter table public.clinica_planos drop column if exists pago_em,
--     drop column if exists nao_fechou_motivo;
