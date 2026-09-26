-- 369_obra_fotos.sql
-- As fotos da obra, por etapa (finance/obra_fotos.py). Desenho aprovado pelo dono
-- em 25/09/2026: docs/mockups/nicho_construcao.html, seções 06 e 07 ("a foto que
-- não é nota"), e item 4 da lista de 26/09/2026.
--
-- O arquivo vai pro bucket PRIVADO (o mesmo dos comprovantes, SUPABASE_BUCKET_DOCS)
-- e o banco guarda só o CAMINHO: é a casa de um cliente, e link público se
-- encaminha. Quem entrega a foto é a rota do Zaq, depois de conferir a sessão (o
-- painel) ou o token do orçamento (o cliente da reforma).
--
-- POR QUE AS FOTOS IMPORTAM: no Reforma Casa Brasil, os últimos 10% do crédito só
-- saem depois que o cliente manda à Caixa as fotos da obra pronta. Sem foto
-- guardada, a última parcela da empresa fica presa.
--
-- Aditiva e idempotente.

create table if not exists public.obra_fotos (
  id            bigserial primary key,
  conta_id      bigint not null references public.contas(id) on delete cascade,
  obra_id       bigint not null references public.obras(id) on delete cascade,
  etapa_chave   text,                               -- null = da obra, sem etapa
  caminho       text   not null,                    -- no bucket privado
  content_type  text   not null,
  bytes         int,
  legenda       text   not null default '',
  origem        text   not null default 'painel' check (origem in ('painel', 'whatsapp')),
  membro_id     bigint,
  criado_em     timestamptz not null default now()
);
create index if not exists idx_obra_fotos_obra on public.obra_fotos (conta_id, obra_id, criado_em);

-- rollback:
--   drop table if exists public.obra_fotos;
