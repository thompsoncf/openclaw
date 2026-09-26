-- 390_ia_marca_visita.sql
-- ETAPA 2 DO VENDEDOR IA (mockup docs/mockups/vendedor_ia_chip.html, versão 5): a IA
-- da regra por número (388) MARCA a visita ao espaço, confirma na véspera e 2h antes,
-- remarca e trata a falta.
--
-- 1. A grade de visita mora NA REGRA do chip, junto com quem recebe o cliente (a
--    anfitriã — a Jacqueline na Prime). Grade nula = a grade de fábrica tirada das
--    visitas que deram certo na Prime (finance/ia_visita.py, GRADE_PADRAO).
-- 2. `ia_visitas`: uma linha por visita que a IA marcou — é o relógio da confirmação
--    (véspera, 2h antes, o dia), a resposta do cliente (1 confirma, 2 remarca), as
--    remarcações (até 2) e o aviso da falta. Tabela à parte, e não colunas em
--    `eventos_agenda`, pra não pedir trava em tabela viva.
-- 3. `ia_ofertas`: os horários que a IA ofereceu com letra (A, B, C) na conversa — é
--    o que transforma o "B" do cliente num horário sem depender da IA ler certo.
--
-- Não toca em `canais_config` nem em nada da conexão (CLAUDE.md §1). Aditiva e
-- idempotente.

alter table public.chip_regra
  add column if not exists visita_marca       boolean  not null default false,
  add column if not exists visita_anfitria_id bigint   references public.membros(id) on delete set null,
  add column if not exists visita_grade       jsonb,
  add column if not exists visita_dur_min     smallint not null default 60,
  add column if not exists visita_folga_min   smallint not null default 30,
  add column if not exists visita_antes_festa_h smallint not null default 3,
  add column if not exists visita_min_h       smallint not null default 3,
  add column if not exists visita_max_dias    smallint not null default 14;

create table if not exists public.ia_visitas (
  evento_id         bigint primary key,
  conta_id          bigint not null references public.contas(id) on delete restrict,
  prospeccao_id     bigint not null,
  conversa_id       bigint,
  -- o horário pede confirmação NO DIA (15h/16h na grade da Prime: 57% vieram)
  conf_no_dia       boolean not null default false,
  vespera_em        timestamptz,     -- a pergunta "1 confirma, 2 remarca" saiu
  duas_horas_em     timestamptz,     -- o lembrete de 2h antes saiu
  confirmado_em     timestamptz,     -- o cliente respondeu 1
  pede_remarcar_em  timestamptz,     -- o cliente respondeu 2 (a IA oferece outros)
  sem_resposta_em   timestamptz,     -- a anfitriã foi avisada de que ninguém confirmou
  falta_em          timestamptz,     -- a mensagem de "sentimos sua falta" saiu
  remarcacoes       smallint not null default 0,
  criado_em         timestamptz not null default now()
);
create index if not exists ia_visitas_conta_idx on public.ia_visitas (conta_id, criado_em desc);
create index if not exists ia_visitas_lead_idx on public.ia_visitas (prospeccao_id);

create table if not exists public.ia_ofertas (
  conversa_id  bigint primary key,
  conta_id     bigint not null references public.contas(id) on delete restrict,
  horarios     timestamptz[] not null,
  criado_em    timestamptz not null default now()
);

-- rollback:
--   drop table if exists public.ia_ofertas;
--   drop table if exists public.ia_visitas;
--   alter table public.chip_regra drop column if exists visita_marca,
--     drop column if exists visita_anfitria_id, drop column if exists visita_grade,
--     drop column if exists visita_dur_min, drop column if exists visita_folga_min,
--     drop column if exists visita_antes_festa_h, drop column if exists visita_min_h,
--     drop column if exists visita_max_dias;
