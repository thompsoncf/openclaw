-- 202610031548_obra_romaneio_e_onde.sql
-- O QUE FAZ: a viagem do caminhão (o romaneio: os pedidos que saíram juntos) e o
--   "onde" de cada material no CD (baia, prateleira).
-- POR QUÊ: PR 3c do CD das obras (docs/mockups/obras_cd_almoxarifado.html, aba
--   "Saídas e romaneio" e a coluna "Onde" do estoque), aprovado pelo dono em
--   03/10/2026: cada viagem é um romaneio — imprime ou manda pro motorista.
--
--   obra_viagens               a viagem: motorista (e o WhatsApp dele, opcional),
--                              quando saiu e quem despachou
--   obra_pedidos.viagem_id     em qual viagem o pedido saiu
--   catalogo_produtos.onde     onde o material fica no CD — texto livre, opcional
--
-- Aditiva e idempotente (if not exists). Banco sem o CD (sem a 032 ou a 670):
-- pula inteira. As LEITURAS tratam a coluna/tabela ausente como "sem viagem" e
-- "sem onde"; o "Saiu" e o salvar dependem dela — o deploy roda as migrações
-- antes do código novo (preDeployCommand do render.yaml).

do $$
begin
  if to_regclass('public.obra_pedidos') is null or to_regclass('public.catalogo_produtos') is null then
    raise notice 'romaneio do CD: sem obra_pedidos (670) ou catálogo (032) — nada a fazer';
    return;
  end if;

create table if not exists public.obra_viagens (
  id            bigserial primary key,
  conta_id      bigint not null references public.contas(id) on delete cascade,
  motorista     text not null default '',
  fone          text not null default '',
  saiu_em       timestamptz not null default now(),
  despachou     bigint references public.membros(id) on delete set null
);
create index if not exists idx_obra_viagens_conta on public.obra_viagens (conta_id, saiu_em desc);

alter table public.obra_pedidos
  add column if not exists viagem_id bigint references public.obra_viagens(id) on delete set null;
create index if not exists idx_obra_pedidos_viagem on public.obra_pedidos (viagem_id) where viagem_id is not null;

alter table public.catalogo_produtos add column if not exists onde text;
end $$;

-- rollback:
--   alter table public.catalogo_produtos drop column if exists onde;
--   alter table public.obra_pedidos drop column if exists viagem_id;
--   drop table if exists public.obra_viagens;
