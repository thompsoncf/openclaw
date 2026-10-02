-- 476_obra_quadras.sql
-- As obras agrupadas por quadra (ou setor, bloco — a empresa escolhe o nome).
-- Desenho aprovado pelo dono em 01/10/2026: docs/mockups/obras_por_quadra.html
-- ("segue as recomendações"). PR 1 de 2: a quadra, a lista agrupada, o quadro de
-- etapas com marcação em lote e o agente. O mapa e o custo comum vêm no PR 2 — o
-- `centro_custo_id` da quadra nasce aqui, vazio, pra não precisar de outra migração.
--
-- Por que existe: quem constrói casa popular trabalha em loteamento. A obra anda em
-- lote (a fundação da quadra inteira, depois a alvenaria de todas), e marcar etapa
-- casa por casa com 20 casas vira trabalho.
--
--   obra_grupos             a quadra: nome, empreendimento (texto — decisão 2 do dono)
--   obras.grupo_id, .lote   a casa na quadra; casa sem quadra continua igual
--   contas.obras_rotulo_grupo  como a empresa chama o grupo (null = "Quadra", decisão 1)
--   obra_grupo_marcacoes    cada marcação em lote, pra "desfaz" voltar a última (decisão 4)
--
-- Aditiva e idempotente.

create table if not exists public.obra_grupos (
  id               bigserial primary key,
  conta_id         bigint not null references public.contas(id) on delete cascade,
  nome             text   not null,
  empreendimento   text   not null default '',
  centro_custo_id  bigint references public.centros_custo(id) on delete set null,
  ordem            int    not null default 0,
  criado_em        timestamptz not null default now()
);
create unique index if not exists uq_obra_grupos_nome on public.obra_grupos (conta_id, lower(nome));

alter table public.obras add column if not exists grupo_id bigint
    references public.obra_grupos(id) on delete set null;
alter table public.obras add column if not exists lote text;
create index if not exists idx_obras_grupo on public.obras (grupo_id) where grupo_id is not null;

alter table public.contas add column if not exists obras_rotulo_grupo text;

create table if not exists public.obra_grupo_marcacoes (
  id           bigserial primary key,
  conta_id     bigint not null references public.contas(id) on delete cascade,
  grupo_id     bigint not null references public.obra_grupos(id) on delete cascade,
  etapa_chave  text   not null,
  obra_ids     jsonb  not null default '[]'::jsonb,
  criado_em    timestamptz not null default now(),
  desfeita_em  timestamptz
);
create index if not exists idx_obra_grupo_marc on public.obra_grupo_marcacoes (conta_id, grupo_id, criado_em desc);

-- rollback:
--   drop table if exists public.obra_grupo_marcacoes;
--   alter table public.contas drop column if exists obras_rotulo_grupo;
--   alter table public.obras drop column if exists lote, drop column if exists grupo_id;
--   drop table if exists public.obra_grupos;
