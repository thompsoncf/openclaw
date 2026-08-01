-- 117_campanha_eventos.sql
-- Histórico (append-only) de envios/engajamento por campanha e lead, com data/hora.
-- Cada linha é um evento no tempo, separável por canal — é o que alimenta o
-- "Desempenho & acompanhamento" (linha do tempo por lead, 📧 e-mail vs 💬 WhatsApp).
--   canal : 'email' | 'whatsapp'
--   evento: enviado | aberto | entregue | lido | respondeu | clicou | bounce
--           | descadastrou | erro
-- Idempotente.

create table if not exists public.campanha_eventos (
  id            bigserial primary key,
  campanha_id   bigint not null references public.campanhas(id) on delete cascade,
  prospeccao_id bigint not null,
  canal         text   not null,
  evento        text   not null,
  detalhe       text,
  quando        timestamptz not null default now()
);
create index if not exists idx_camp_eventos
  on public.campanha_eventos (campanha_id, prospeccao_id, quando);
