-- 202610031404_obra_ferramentas.sql
-- O QUE FAZ: as ferramentas e equipamentos do CD — cada unidade com código
--   (FER-01), e o empréstimo: em qual obra está, com quem e desde quando.
-- POR QUÊ: PR 2 de 3 do CD das obras (docs/mockups/obras_cd_almoxarifado.html,
--   aba "Ferramentas"), aprovado pelo dono em 03/10/2026. Decisão 5: a
--   ferramenta entra no MESMO catálogo do material (catalogo_produtos, categoria
--   'ferramenta') — um cadastro só —, mas se comporta como empréstimo: SAI E
--   VOLTA, nunca é baixa de estoque.
--
--   obra_ferramentas        a unidade de patrimônio: o tipo (catálogo), o código
--                           único na empresa, a observação, e a baixa (quebrou,
--                           perdeu) com o motivo
--   obra_ferramenta_movs    cada saída: pra qual obra, com quem, quando saiu e
--                           quando voltou. A que não voltou (voltou_em vazio) diz
--                           onde a ferramenta está; sem nenhuma aberta, está no CD
--
-- Uma ferramenta só pode estar fora UMA vez (índice único parcial): mandar pra
-- outra obra fecha a saída anterior antes de abrir a nova.
--
-- Aditiva e idempotente (if not exists / on conflict do nothing).

create table if not exists public.obra_ferramentas (
  id           bigserial primary key,
  conta_id     bigint not null references public.contas(id) on delete cascade,
  produto_id   bigint not null references public.catalogo_produtos(id),
  codigo       text   not null,
  obs          text   not null default '',
  ativa        boolean not null default true,
  baixa_motivo text,
  baixa_em     timestamptz,
  criado_em    timestamptz not null default now()
);
create unique index if not exists uq_obra_ferramentas_codigo
    on public.obra_ferramentas (conta_id, upper(codigo));

create table if not exists public.obra_ferramenta_movs (
  id              bigserial primary key,
  conta_id        bigint not null references public.contas(id) on delete cascade,
  ferramenta_id   bigint not null references public.obra_ferramentas(id) on delete cascade,
  obra_id         bigint references public.obras(id) on delete set null,
  com_quem        text   not null default '',
  saiu_por        bigint references public.membros(id) on delete set null,
  saiu_em         timestamptz not null default now(),
  voltou_em       timestamptz,
  voltou_por      bigint references public.membros(id) on delete set null
);
create unique index if not exists uq_obra_ferramenta_fora
    on public.obra_ferramenta_movs (ferramenta_id) where voltou_em is null;
create index if not exists idx_obra_ferramenta_movs on public.obra_ferramenta_movs (conta_id, obra_id);

-- rollback:
--   drop table if exists public.obra_ferramenta_movs;
--   drop table if exists public.obra_ferramentas;
