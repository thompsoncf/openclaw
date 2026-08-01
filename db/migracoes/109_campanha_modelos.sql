-- 109_campanha_modelos.sql
-- Biblioteca de modelos de sequência por nicho (pet shop, frutaria, clínica…).
-- Ao criar/configurar a campanha, o dono escolhe um modelo e a sequência é
-- COPIADA (snapshot) pra campanha — cada campanha segue dona da própria abordagem.
-- Os passos ficam em jsonb: [{"dias":0,"assunto":"...","corpo":"...","ia":false}, ...]
-- Idempotente.

create table if not exists public.campanha_modelos (
  id            bigserial primary key,
  conta_id      bigint not null references public.contas(id) on delete cascade,
  codigo        text   not null,                 -- "referência" do modelo (slug do nicho)
  nome          text   not null,
  passos        jsonb  not null default '[]'::jsonb,
  wa_texto      text,
  criado_em     timestamptz not null default now(),
  atualizado_em timestamptz not null default now()
);
create unique index if not exists idx_modelos_conta_codigo
  on public.campanha_modelos (conta_id, codigo);

-- de qual modelo a campanha partiu (informativo; a sequência é cópia própria)
alter table public.campanhas add column if not exists modelo_codigo text;
