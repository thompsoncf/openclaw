-- 331_perda_lida_detalhe.sql
-- A leitura do motivo da perda (328) passa a dizer DUAS coisas a mais, e o
-- gestor passa a poder corrigir o motivo. Pedido do dono em 24/09/2026, ao
-- aprovar o mockup "Origens no app e no computador":
--
--   perda_lida_parou       em que ponto a conversa parou: 'antes_do_preco',
--                          'depois_do_preco' ou 'depois_da_proposta'. É o que
--                          separa o "não respondeu" que sumiu sem ver valor do
--                          que viu o valor e sumiu — dois problemas diferentes.
--   perda_lida_quem        pra quem NÃO ERA CLIENTE, o que a pessoa queria:
--                          'emprego', 'fornecedor', 'item_avulso', 'doacao',
--                          'engano' ou 'outro'. É o número que a gestão de
--                          tráfego usa pra ajustar o público do anúncio.
--   perda_lida_versao      qual versão da leitura gravou a linha. As lidas pela
--                          328 ficam na 1 e são relidas uma vez pra ganhar os dois
--                          campos acima; `finance.motivo_lido.VERSAO` diz a atual.
--   perda_corrigida_por    o membro que corrigiu o motivo pela lista de perdidos
--   perda_corrigida_em     e quando. A correção grava em `perda_motivo` (vale como
--                          o do vendedor); estas duas dizem que foi correção.
--
-- Aditivo: colunas anuláveis ou com default constante, sem reescrever a tabela.
-- Nada existente muda de valor.

alter table public.prospeccao
  add column if not exists perda_lida_parou text,
  add column if not exists perda_lida_quem text,
  add column if not exists perda_lida_versao smallint not null default 1,
  add column if not exists perda_corrigida_por integer,
  add column if not exists perda_corrigida_em timestamptz;

-- rollback:
--   alter table public.prospeccao
--     drop column if exists perda_lida_parou, drop column if exists perda_lida_quem,
--     drop column if exists perda_lida_versao, drop column if exists perda_corrigida_por,
--     drop column if exists perda_corrigida_em;
