-- 371_obra_etapa_pagamentos.sql
-- O que já foi pago ao empreiteiro por etapa (finance/obra_empreita.py). Desenho
-- aprovado pelo dono em 25/09/2026: docs/mockups/nicho_construcao.html, seção 07
-- ("paguei 10 mil pro empreiteiro, primeira etapa da casa 2" -> "Marquei fundação
-- e estrutura como pagas"), item 5 da lista de 26/09/2026.
--
-- POR QUE: em obra de empreitada, a mão de obra se paga por etapa. O que dá
-- prejuízo é pagar o que não foi feito (adiantamento que vira calote) e pagar a
-- mesma etapa duas vezes. Cruzando "feita" (obra_etapas.concluida_em) com "paga"
-- (aqui), a ficha mostra os dois — é a medição do CC, art. 614.
--
-- UMA LINHA POR ETAPA E PAGAMENTO: o pagamento que fecha duas etapas vira duas
-- linhas, com o valor dividido pelo peso delas; a etapa paga em duas vezes tem
-- duas linhas. `lancamento_id` aponta pro lançamento de mão de obra da obra (o
-- dinheiro já está no caixa por ele; aqui é só a quem e pelo quê).
--
-- Tabela própria, e não colunas em obra_etapas: a leitura das etapas é de toda
-- tela de obra, e ela não precisa saber de pagamento.
--
-- Aditiva e idempotente.

create table if not exists public.obra_etapa_pagamentos (
  id              bigserial primary key,
  conta_id        bigint not null references public.contas(id) on delete cascade,
  obra_id         bigint not null references public.obras(id) on delete cascade,
  etapa_id        bigint not null references public.obra_etapas(id) on delete cascade,
  lancamento_id   bigint references public.lancamentos(id) on delete set null,
  valor_centavos  bigint not null check (valor_centavos >= 0),
  pago_em         date   not null default current_date,
  obs             text   not null default '',
  criado_em       timestamptz not null default now()
);
create index if not exists idx_obra_etapa_pag_obra on public.obra_etapa_pagamentos (conta_id, obra_id);
create index if not exists idx_obra_etapa_pag_lanc on public.obra_etapa_pagamentos (lancamento_id);

-- rollback:
--   drop table if exists public.obra_etapa_pagamentos;
