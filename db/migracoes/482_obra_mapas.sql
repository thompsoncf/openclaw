-- 482_obra_mapas.sql
-- O mapa das obras: a planta do empreendimento com os lotes riscados em cima.
-- Desenho aprovado pelo dono em 02/10/2026 (docs/mockups/obras_mapa_3d.html,
-- "segue as recomendações"). PR 1 de 3: a área, a planta, o editor de riscar,
-- o mapa 2D/3D e o perfil com a casa por camadas. Material e app do mestre vêm
-- nos PRs 2 e 3.
--
-- Por que existe: quem constrói em loteamento enxerga a obra pelo mapa, não por
-- lista. A planta real (PDF ou foto) vira o fundo; os lotes são retângulos
-- riscados pela própria empresa — extrair lote de PDF de loteamento
-- automaticamente não funciona (a planta real que testamos tem os lotes em 362
-- segmentos soltos, nenhum retângulo fechado).
--
--   obra_mapas        a área de obras (o empreendimento): nome, cidade, a planta
--                     no bucket PRIVADO (caminho, nunca URL) e a altura do
--                     desenho na unidade-1000 (largura é sempre 1000)
--   obra_mapa_lotes   cada risco: posição x/y/larg/alt na unidade-1000, rótulo,
--                     situação (meu / vago / de terceiro — o caso real "essa
--                     azul e rosa não é minha") e a obra ligada, quando tem
--   obra_grupos.mapa_id   a quadra pertence a uma área (vínculo usado adiante)
--
-- Uma casa só pode estar num lote (índice único parcial): o mesmo endereço em
-- dois riscos seria dois andamentos pra mesma obra no mapa.
--
-- Aditiva e idempotente.

create table if not exists public.obra_mapas (
  id              bigserial primary key,
  conta_id        bigint not null references public.contas(id) on delete cascade,
  nome            text   not null,
  cidade          text   not null default '',
  planta_caminho  text,
  altura          int    not null default 520,
  criado_em       timestamptz not null default now()
);
create unique index if not exists uq_obra_mapas_nome on public.obra_mapas (conta_id, lower(nome));

create table if not exists public.obra_mapa_lotes (
  id         bigserial primary key,
  conta_id   bigint not null references public.contas(id) on delete cascade,
  mapa_id    bigint not null references public.obra_mapas(id) on delete cascade,
  obra_id    bigint references public.obras(id) on delete set null,
  rotulo     text   not null default '',
  x          int    not null,
  y          int    not null,
  larg       int    not null,
  alt        int    not null,
  situacao   text   not null default 'meu' check (situacao in ('meu', 'vago', 'terceiro')),
  criado_em  timestamptz not null default now()
);
create index if not exists idx_obra_mapa_lotes on public.obra_mapa_lotes (mapa_id);
create unique index if not exists uq_obra_mapa_lotes_obra
    on public.obra_mapa_lotes (obra_id) where obra_id is not null;

alter table public.obra_grupos add column if not exists mapa_id bigint
    references public.obra_mapas(id) on delete set null;

-- rollback:
--   alter table public.obra_grupos drop column if exists mapa_id;
--   drop table if exists public.obra_mapa_lotes;
--   drop table if exists public.obra_mapas;
