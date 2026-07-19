-- 075_modulo_prospeccao.sql
-- Modulo de prospeccao: lista de alvos (prospeccao) + historico de contatos (prospeccao_atividades).
-- Vendedores = membros com papel 'vendedor' (papel e texto livre, sem migracao).
-- Conversao: lead quente gera um orcamento (orcamento_id). Idempotente (if not exists).
-- Obs: numerada 075 porque 068..074 ja existem no repo (o rascunho vinha como 068).

create table if not exists public.prospeccao (
  id bigserial primary key,
  conta_id bigint not null references public.contas(id),
  vendedor_id bigint references public.membros(id),
  empresa text not null,
  cnpj text,
  segmento text,
  cidade text,
  uf varchar(2),
  contato text,
  cargo text,
  telefone text,
  whatsapp text,
  email text,
  status text not null default 'novo'
    check (status in ('novo','contatado','qualificado','proposta','ganho','perdido')),
  temperatura text not null default 'frio'
    check (temperatura in ('frio','morno','quente')),
  valor_estimado_centavos bigint not null default 0,
  origem text,
  obs text,
  place_id text,
  tem_site boolean,
  instagram text,
  socio text,
  regime_tributario text,
  porte text,
  ultimo_contato_em timestamptz,
  proximo_contato_em timestamptz,
  orcamento_id bigint references public.orcamentos(id),
  criado_por bigint references public.membros(id),
  criado_em timestamptz not null default now(),
  atualizado_em timestamptz not null default now()
);

create index if not exists idx_prospeccao_conta on public.prospeccao (conta_id);
create index if not exists idx_prospeccao_vendedor on public.prospeccao (vendedor_id);
create index if not exists idx_prospeccao_status on public.prospeccao (conta_id, status);
create index if not exists idx_prospeccao_proximo on public.prospeccao (proximo_contato_em);
create unique index if not exists uq_prospeccao_conta_cnpj on public.prospeccao (conta_id, cnpj) where cnpj is not null;
create unique index if not exists uq_prospeccao_conta_place on public.prospeccao (conta_id, place_id) where place_id is not null;

create table if not exists public.prospeccao_atividades (
  id bigserial primary key,
  prospeccao_id bigint not null references public.prospeccao(id) on delete cascade,
  membro_id bigint references public.membros(id),
  tipo text not null
    check (tipo in ('ligacao','whatsapp','email','reuniao','visita','nota')),
  resultado text
    check (resultado in ('sem_resposta','retornar','interessado','sem_interesse','agendado','fechado')),
  descricao text not null default '',
  agendado_para timestamptz,
  criado_em timestamptz not null default now()
);

create index if not exists idx_prosp_ativ_prospeccao on public.prospeccao_atividades (prospeccao_id);

alter table public.prospeccao enable row level security;
alter table public.prospeccao_atividades enable row level security;
