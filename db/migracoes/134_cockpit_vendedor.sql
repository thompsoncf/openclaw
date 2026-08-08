-- 134_cockpit_vendedor.sql
-- Cockpit do Vendedor: app mobile (PWA) só pro vendedor RECEBER e TRABALHAR o lead
-- fora do painel completo do Zaq. Login por LINK MÁGICO (sem senha): o gestor gera o
-- link (ou o vendedor pede pelo próprio e-mail) e o token abre a sessão do membro.
--   * cockpit_acesso : token curto (15 min), 1 uso, aponta pro membro/conta.
--   * membros.cockpit_push_ativo : o vendedor quer receber push (PWA) — default sim.
--   * membros.cockpit_pausado    : o vendedor pausou o rodízio (não recebe leads novos
--                                  agora) sem sair da fila que o gestor montou.
-- Aditivo e idempotente (só cria tabela/colunas — não mexe em nada existente).

create table if not exists public.cockpit_acesso (
  token      text primary key,
  conta_id   bigint not null references public.contas(id)  on delete cascade,
  membro_id  bigint not null references public.membros(id) on delete cascade,
  expira_em  timestamptz not null,
  usado_em   timestamptz,
  criado_em  timestamptz not null default now()
);
create index if not exists idx_cockpit_acesso_membro on public.cockpit_acesso (membro_id);

alter table public.membros add column if not exists cockpit_push_ativo boolean not null default true;
alter table public.membros add column if not exists cockpit_pausado    boolean not null default false;
