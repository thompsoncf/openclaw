-- 081_canais_config.sql
-- Número/identificador de cada canal POR EMPRESA (conta_id) — não dá pra usar env
-- global (TWILIO_WHATSAPP_FROM é único e já é do Zaq). Assim cada empresa tem o
-- seu número e o inbound roteia pelo número que recebeu (To). As credenciais da
-- conta Twilio seguem globais (env); só o "from" é por empresa. Idempotente.

create table if not exists public.canais_config (
  id             bigserial primary key,
  conta_id       bigint not null references public.contas(id) on delete cascade,
  canal          text not null check (canal in ('whatsapp','messenger','instagram')),
  identificador  text not null,               -- ex: 'whatsapp:+17602847678'
  ativo          boolean not null default true,
  criado_em      timestamptz not null default now(),
  atualizado_em  timestamptz not null default now()
);
-- uma empresa tem um identificador por canal; e um número pertence a uma empresa só
create unique index if not exists idx_canais_conta_canal on public.canais_config (conta_id, canal);
create unique index if not exists idx_canais_ident on public.canais_config (canal, identificador);
