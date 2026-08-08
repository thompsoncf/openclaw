-- 135_push_assinaturas.sql
-- Assinaturas de Web Push do Cockpit: cada navegador/aparelho que o vendedor
-- autorizou vira uma linha (endpoint + chaves do RFC 8291). Um vendedor pode ter
-- várias (celular, desktop). Quando cai um lead pra ele, a gente dispara push pra
-- todas as assinaturas ativas dele (se cockpit_push_ativo). Endpoint morto (404/410)
-- é apagado no envio. Aditivo e idempotente.

create table if not exists public.push_assinaturas (
  id         bigserial primary key,
  conta_id   bigint not null references public.contas(id)  on delete cascade,
  membro_id  bigint not null references public.membros(id) on delete cascade,
  endpoint   text not null unique,
  p256dh     text not null,
  auth       text not null,
  criado_em  timestamptz not null default now()
);
create index if not exists idx_push_assinaturas_membro on public.push_assinaturas (membro_id);
