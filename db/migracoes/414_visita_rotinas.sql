-- 414_visita_rotinas.sql
-- AS ROTINAS DA VISITA — o funil novo de eventos, parte 2a (docs/mockups/
-- funil_novo_rotinas.html, aprovado pelo dono em 27/09/2026 "com as recomendações").
-- O motor é finance/visita_rotinas.py.
--
-- 1. `visita_rotinas_config`: as três chaves da conta (Funil › Régua › Rotinas de
--    festa). NASCEM DESLIGADAS: conta sem linha = nada acontece. `ligado_em` é o marco
--    do "sua visita está marcada": só visita criada depois dele recebe — ligar não pode
--    virar uma rajada pras visitas de semanas atrás.
--
-- 2. `visita_rotinas`: uma linha por visita acompanhada, com o instante de cada passo.
--    A coluna nula vira agora ANTES do envio (reivindicação): dois workers nunca mandam
--    a mesma mensagem duas vezes. `da_ia` é só o registro de quando entrou; quem decide
--    é a leitura de `ia_visitas` a cada ciclo.
--
-- 3. A PRIME (conta 34) nasce com as três ligadas, com a autorização do dono no mockup.
--    Medido em 27/09/2026 (só leitura): 24% de falta nas visitas dos últimos 60 dias
--    (6 de 25), nenhuma confirmada; as 29 marcadas pela equipe.
--
-- 4. A "IA insiste" no chip Thiago (conta 34, chip 36): decisão 6 do mockup ("ligar
--    pela migração da parte 2a, com esta aprovação"). Medido: 0 leads do chip hoje —
--    ligar não dispara nada de uma vez. E a IA insistir não fecha lead antigo: o
--    perdido só vem depois de dois toques (finance/ia_insiste.py).
--
-- Não toca conversa, mensagem, conexão, pareamento nem `canais_config` (CLAUDE.md
-- §0/§1). Aditiva e idempotente.

create table if not exists public.visita_rotinas_config (
  conta_id        bigint primary key,
  confirmar       boolean not null default false,
  perguntar_veio  boolean not null default false,
  depois_visita   boolean not null default false,
  ligado_em       timestamptz,
  atualizado_em   timestamptz not null default now()
);

create table if not exists public.visita_rotinas (
  evento_id        bigint primary key,
  conta_id         bigint not null,
  prospeccao_id    bigint,
  da_ia            boolean not null default false,
  -- o horário a que os passos se referem: remarcou (o mesmo evento, outro horário),
  -- os passos recomeçam
  inicio_visto     timestamptz,
  ao_marcar_em     timestamptz,
  vespera_em       timestamptz,
  duas_horas_em    timestamptz,
  confirmado_em    timestamptz,
  pede_remarcar_em timestamptz,
  sem_resposta_em  timestamptz,
  veio_1_em        timestamptz,
  veio_2_em        timestamptz,
  depois_em        timestamptz,
  depois_acao      text,
  falta_aviso_em   timestamptz,
  envio_falhas     integer not null default 0,
  envio_falhou_em  timestamptz,
  criado_em        timestamptz not null default now()
);

create index if not exists visita_rotinas_conta_lead
  on public.visita_rotinas (conta_id, prospeccao_id);

comment on table public.visita_rotinas is
  'as rotinas da visita (finance/visita_rotinas.py): o instante de cada passo, por visita';

-- ── a Prime (34) ─────────────────────────────────────────────────────────────
insert into public.visita_rotinas_config (conta_id, confirmar, perguntar_veio, depois_visita,
                                          ligado_em)
select 34, true, true, true, now()
 where exists (select 1 from public.contas where id = 34)
on conflict (conta_id) do nothing;

update public.chip_regra set ia_insiste = true, atualizado_em = now()
 where conta_id = 34 and chip_id = 36 and ativa and ia_ligada and not ia_insiste;

-- rollback:
--   update public.visita_rotinas_config set confirmar=false, perguntar_veio=false,
--          depois_visita=false where conta_id = 34;
--   update public.chip_regra set ia_insiste = false where conta_id = 34 and chip_id = 36;
--   (as tabelas podem ficar: sem linha ligada, o motor não faz nada)
