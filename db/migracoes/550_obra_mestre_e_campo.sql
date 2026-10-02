-- 550_obra_mestre_e_campo.sql
-- O app do mestre de obras (PR 3 do desenho docs/mockups/obras_mapa_3d.html,
-- seção 4, aprovado pelo dono em 02/10/2026: "o mestre marca direto, com
-- desfazer e aviso ao dono; o app não mostra dinheiro nenhum").
--
--   obras.mestre_id   o mestre responsável pela obra (um membro com papel
--                     'mestre'): é o que faz o app mostrar SÓ as obras dele
--   obra_campo        cada gesto do campo (etapa marcada, foto, material
--                     apontado): é o "aviso ao dono" (a lista "Do campo" na tela
--                     de Obras) e o que o "desfazer" desfaz
--   obra_fotos.origem ganha 'campo' (a foto que veio do app)
--
-- NÚMERO 550: o maior em uso em main + PRs abertos era 520 — pulo pra longe
-- (regra de 02/10, depois de 482–485 colidirem).
--
-- Aditiva e idempotente.

alter table public.obras add column if not exists mestre_id bigint
    references public.membros(id) on delete set null;
create index if not exists idx_obras_mestre on public.obras (mestre_id) where mestre_id is not null;

create table if not exists public.obra_campo (
  id           bigserial primary key,
  conta_id     bigint not null references public.contas(id) on delete cascade,
  obra_id      bigint not null references public.obras(id) on delete cascade,
  membro_id    bigint references public.membros(id) on delete set null,
  tipo         text   not null check (tipo in ('etapa', 'foto', 'material')),
  ref          jsonb  not null default '{}'::jsonb,
  descricao    text   not null default '',
  criado_em    timestamptz not null default now(),
  desfeito_em  timestamptz
);
create index if not exists idx_obra_campo_conta on public.obra_campo (conta_id, criado_em desc);

alter table public.obra_fotos drop constraint if exists obra_fotos_origem_check;
alter table public.obra_fotos add constraint obra_fotos_origem_check
    check (origem in ('painel', 'whatsapp', 'campo'));

-- rollback:
--   alter table public.obra_fotos drop constraint if exists obra_fotos_origem_check;
--   alter table public.obra_fotos add constraint obra_fotos_origem_check
--       check (origem in ('painel', 'whatsapp'));
--   drop table if exists public.obra_campo;
--   alter table public.obras drop column if exists mestre_id;
