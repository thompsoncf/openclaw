-- 086_campanhas_email.sql
-- Fase 2: motor de e-mail em sequência (campanhas frias). Idempotente.
--   campanhas       — cada campanha (nome, status, limite/dia).
--   campanha_passos — os passos da sequência (D0/D3/D7; IA ou template).
--   campanha_alvos  — os leads na campanha e o progresso deles.
--   descadastros    — supressão por conta (LGPD: quem pediu pra não receber).

create table if not exists public.campanhas (
  id            bigserial primary key,
  conta_id      bigint not null references public.contas(id) on delete cascade,
  nome          text not null,
  status        text not null default 'rascunho'
                  check (status in ('rascunho','ativa','pausada','concluida')),
  limite_dia    int not null default 40,
  criado_por    bigint,
  criado_em     timestamptz not null default now(),
  atualizado_em timestamptz not null default now()
);
create index if not exists idx_campanhas_conta on public.campanhas (conta_id, status);

create table if not exists public.campanha_passos (
  id            bigserial primary key,
  campanha_id   bigint not null references public.campanhas(id) on delete cascade,
  ordem         int not null,
  dias_apos     int not null default 0,
  assunto       text,
  corpo         text,
  usar_ia       boolean not null default false
);
create unique index if not exists idx_passos_campanha_ordem on public.campanha_passos (campanha_id, ordem);

create table if not exists public.campanha_alvos (
  id              bigserial primary key,
  campanha_id     bigint not null references public.campanhas(id) on delete cascade,
  prospeccao_id   bigint not null references public.prospeccao(id) on delete cascade,
  status          text not null default 'fila'
                    check (status in ('fila','enviado','respondeu','descadastrou','erro','concluido')),
  passo_atual     int not null default 0,
  proximo_envio_em timestamptz,
  ultima_msg_em   timestamptz,
  criado_em       timestamptz not null default now()
);
create unique index if not exists idx_alvos_campanha_lead on public.campanha_alvos (campanha_id, prospeccao_id);
create index if not exists idx_alvos_fila on public.campanha_alvos (status, proximo_envio_em);

create table if not exists public.descadastros (
  id         bigserial primary key,
  conta_id   bigint not null references public.contas(id) on delete cascade,
  email      text not null,
  token      text,
  criado_em  timestamptz not null default now()
);
create unique index if not exists idx_descad_conta_email on public.descadastros (conta_id, lower(email));
create index if not exists idx_descad_token on public.descadastros (token);
