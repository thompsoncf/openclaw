-- 375_obra_do_lead.sql
-- A reforma ligada ao lead que a vendeu (finance/obra_lead.py). Desenho aprovado
-- pelo dono em 25/09/2026: docs/mockups/nicho_construcao.html, seções 08 e 09;
-- item 7 da lista de 26/09/2026.
--
-- Sem o laço, o funil e a obra contam duas histórias que não se encontram: o lead
-- da reforma ficava em "Proposta" pra sempre depois de o cliente aceitar o
-- orçamento, e a cobrança não sabia o WhatsApp de quem cobrar.
--
-- Com ele: o orçamento enviado põe o card em Proposta, o aceito em Fechado (ou em
-- Crédito em análise, no Reforma Casa Brasil, que ainda espera a Caixa), e a ficha
-- da obra abre o lead.
--
-- Aditiva e idempotente. `on delete set null`: apagar o lead não apaga a obra.

alter table public.obras add column if not exists prospeccao_id bigint
    references public.prospeccao(id) on delete set null;
create index if not exists idx_obras_prospeccao on public.obras (prospeccao_id)
    where prospeccao_id is not null;

-- rollback:
--   drop index if exists public.idx_obras_prospeccao;
--   alter table public.obras drop column if exists prospeccao_id;
