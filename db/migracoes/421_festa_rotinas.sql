-- 421_festa_rotinas.sql
-- A PROPOSTA, A DATA SEGURADA E O PÓS-FESTA — o funil novo de eventos, parte 2b
-- (docs/mockups/funil_novo_rotinas.html, aprovado pelo dono em 27/09/2026 "com as
-- recomendações"). O motor é finance/festa_rotinas.py.
--
-- 1. Os números da conta, na mesma linha das rotinas da visita (Funil › Régua ›
--    Rotinas de festa — o dono muda quando quiser):
--      proposta_validade_dias  quantos dias a proposta vale (null = sem regra: o link
--                              segue mostrando a data da festa, como sempre)
--      validade_desde          o marco: só proposta EMITIDA depois dele segue o número
--      reserva_disputada_h     outro cliente pediu a data segurada → a reserva passa a
--                              vencer em tantas horas (null = não encurta)
--      pos_festa, pos_festa_desde, avaliacao_link   o dia seguinte à festa
-- 2. `data_disputas`: uma linha por reserva disputada (o aviso sai uma vez, e o prazo
--    de antes fica guardado). `pos_festa_envios`: uma linha por festa (a mensagem sai
--    uma vez).
-- 3. A PRIME (34): 7 dias de validade (decisão 5: a mesma que a IA já fala), 48h na
--    disputa (decisão 1) e o pós-festa ligado. O link de avaliação fica em branco: o
--    dono cola na Régua quando quiser; sem ele, a mensagem sai sem a avaliação.
--
-- Não toca conversa, mensagem, conexão, chip nem `canais_config` (CLAUDE.md §0/§1), nem
-- card nenhum. Aditiva e idempotente.

alter table public.visita_rotinas_config
  add column if not exists proposta_validade_dias integer,
  add column if not exists validade_desde        timestamptz,
  add column if not exists reserva_disputada_h   integer,
  add column if not exists pos_festa             boolean not null default false,
  add column if not exists pos_festa_desde       timestamptz,
  add column if not exists avaliacao_link        text;

create table if not exists public.data_disputas (
  evento_id      bigint primary key,
  conta_id       bigint not null,
  prospeccao_id  bigint,
  quem_pediu     bigint,
  prazo_antes    timestamptz,
  prazo_novo     timestamptz,
  criado_em      timestamptz not null default now()
);

create table if not exists public.pos_festa_envios (
  prospeccao_id  bigint primary key,
  conta_id       bigint not null,
  quem           text,
  criado_em      timestamptz not null default now()
);

-- ── a Prime (34) ─────────────────────────────────────────────────────────────
insert into public.visita_rotinas_config (conta_id) select 34
 where exists (select 1 from public.contas where id = 34)
on conflict (conta_id) do nothing;

update public.visita_rotinas_config
   set proposta_validade_dias = 7, validade_desde = now(),
       reserva_disputada_h = 48,
       pos_festa = true, pos_festa_desde = now()
 where conta_id = 34 and proposta_validade_dias is null and validade_desde is null
   and reserva_disputada_h is null and not pos_festa;

-- rollback:
--   update public.visita_rotinas_config set proposta_validade_dias=null, validade_desde=null,
--          reserva_disputada_h=null, pos_festa=false where conta_id = 34;
--   (os prazos encurtados estão em data_disputas.prazo_antes)
