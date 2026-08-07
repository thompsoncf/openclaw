-- 130_funil_etapas.sql
-- Etapas do funil personalizáveis por conta (renomear/adicionar/remover/reordenar).
-- Antes eram fixas no código (STATUS). Cada conta passa a ter suas próprias etapas
-- em funil_etapas; o prospeccao.status guarda a CHAVE da etapa (estável ao renomear).
--
-- 'novo' (entrada), 'ganho' e 'perdido' (resultado) são fixas: podem ser renomeadas,
-- mas não removidas nem reordenadas — o resto do sistema depende delas (lead nasce em
-- 'novo'; relatórios usam ganho/perdido). Ver web/painel_prospeccao._etapas.
--
-- Também derruba o CHECK antigo de prospeccao.status (que travava nos 6 valores fixos),
-- senão uma etapa nova de chave própria não poderia ser gravada no status do lead.

create table if not exists public.funil_etapas (
  id         bigserial primary key,
  conta_id   bigint not null references public.contas(id) on delete cascade,
  chave      text   not null,               -- o que fica em prospeccao.status
  rotulo     text   not null,               -- o nome que o cliente vê/edita
  ordem      int    not null default 0,
  fixa       boolean not null default false, -- novo/ganho/perdido: renomeia, não remove
  criado_em  timestamptz not null default now(),
  unique (conta_id, chave)
);
create index if not exists idx_funil_etapas_conta on public.funil_etapas (conta_id, ordem);

alter table public.prospeccao drop constraint if exists prospeccao_status_check;
