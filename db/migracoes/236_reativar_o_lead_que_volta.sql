-- 236_reativar_o_lead_que_volta.sql
-- O cliente perdido que volta a falar reabre o MESMO cadastro. Regra 5 do
-- FLUXO_FINAL_FUNIL_PRIME_EVENTOS_DESENVOLVEDOR_V3.
--
-- O QUE O DONO PEDIU
--   "O lead perdido não deve ser apagado. Ele permanece no banco de dados para
--    análise dos motivos de perda e eventual reativação futura. Se um cliente
--    classificado como Perdido voltar a entrar em contato, o sistema deve reativar o
--    mesmo cadastro, preservando todo o histórico anterior, e encaminhá-lo novamente
--    para CONTACTADO ou NEGOCIAÇÃO, conforme a situação."
--
-- O QUE ACONTECE HOJE, E POR QUE NÃO APARECE COMO BUG
-- Nada. Quando um lead perdido manda mensagem, a conversa dele já existe e já está
-- ligada ao cadastro — então o caminho de "lead novo" do inbound não roda, o
-- `_promover_para_lead` só esquenta a temperatura, e o card fica em Perdido pra
-- sempre. A mensagem chega no inbox, o vendedor pode até responder, e o funil
-- continua dizendo que aquele cliente está perdido. Medido na conta 34 em
-- 11/09/2026: 19 leads em Perdido, e UM único movimento manual de volta
-- (perdido → contatado) em 24 dias de histórico — alguém fez na mão.
--
-- POR QUE É UMA COLUNA DA ETAPA, E NÃO "se status = perdido"
-- Mesma razão do teto (230), das saídas (232) e dos toques (233): a regra é da
-- Prime, a mecânica é de todo mundo. `reativa_para` diz DE ONDE e PARA ONDE numa
-- coluna só — a etapa que a declara é a que reabre, e o valor é a etapa de destino.
-- Uma empresa reabre de "Perdido" pra "Contatado"; outra reabre de "Arquivado" pra
-- "Negociação"; a maioria não reabre nada, que é o padrão.
--
-- NULL = NÃO REATIVA, e é como todas as contas nascem. Não existe interruptor
-- separado: a coluna vazia já é o desligado.
--
-- O QUE A REATIVAÇÃO NÃO FAZ
-- Não apaga o motivo da perda, não apaga `perda_em` nem `perda_etapa`, e não toca
-- em mensagem nenhuma. "Preservando todo o histórico anterior" é literal: o lead
-- volta com a história inteira, inclusive a de ter sido perdido uma vez — que é
-- justamente o que o dono quer poder analisar depois.
--
-- Aditiva e idempotente.

alter table public.funil_etapas
  -- chave da etapa pra onde o lead volta quando o cliente fala de novo estando
  -- NESTA etapa. NULL = não reativa (o padrão de toda conta).
  add column if not exists reativa_para text;

comment on column public.funil_etapas.reativa_para is
  'etapa de destino quando um lead PARADO nesta etapa volta a falar; null = não reativa';

-- rollback:
--   alter table public.funil_etapas drop column reativa_para;
