-- 130_conteudo_ia_insta.sql
-- Módulo "IA Insta" (aba de Prospecção): gerar conteúdo com IA, conferir e
-- publicar no Instagram/Facebook via Graph API. Espelha o desenho de campanhas:
-- a tabela guarda o estado, o motor (web/painel_conteudo.py) roda no poller.
--
-- Fluxo do status:
--   rascunho -> revisar -> agendado -> publicando -> publicado
--                                   \-> erro (com erro_msg; dá pra reenfileirar)
--
-- Atribuição: cada post tem um `token` curto. O link do post aponta pra /p/<token>,
-- que conta o clique e redireciona pro WhatsApp (ou /tenho-interesse). Quem chega
-- por ali vira lead em prospeccao (origem='instagram'/'facebook') — é o que liga
-- conteúdo ao funil que já existe.

create table if not exists public.conteudo_posts (
  id             bigserial primary key,
  conta_id       bigint not null,
  criado_por     bigint,                       -- membros.id
  formato        text not null default 'carrossel',   -- carrossel | post
  objetivo       text not null default 'atrair',      -- atrair | provar | ofertar | bastidor
  tema           text,
  legenda        text,
  destino        text not null default 'whatsapp',    -- whatsapp | link | nenhum
  canais         text not null default 'instagram',   -- csv: instagram,facebook
  status         text not null default 'rascunho',
  agendado_para  timestamptz,
  publicado_em   timestamptz,
  ig_media_id    text,
  fb_post_id     text,
  erro_msg       text,
  tentativas     int not null default 0,
  token          text,
  cliques        int not null default 0,
  leads          int not null default 0,
  criado_em      timestamptz not null default now(),
  atualizado_em  timestamptz not null default now()
);

create index if not exists idx_conteudo_posts_conta
  on public.conteudo_posts (conta_id, status);
create index if not exists idx_conteudo_posts_fila
  on public.conteudo_posts (status, agendado_para)
  where status in ('agendado', 'publicando');
create unique index if not exists ux_conteudo_posts_token
  on public.conteudo_posts (token) where token is not null;

create table if not exists public.conteudo_midias (
  id            bigserial primary key,
  post_id       bigint not null references public.conteudo_posts(id) on delete cascade,
  ordem         int not null default 0,
  url           text not null,              -- precisa ser pública: a Meta baixa a imagem
  texto         text,                       -- o texto do card (histórico do que a IA escreveu)
  container_id  text,                       -- id do container do Instagram
  criado_em     timestamptz not null default now()
);

create index if not exists idx_conteudo_midias_post
  on public.conteudo_midias (post_id, ordem);
