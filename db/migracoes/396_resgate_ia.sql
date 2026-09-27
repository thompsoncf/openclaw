-- 396_resgate_ia.sql
-- O RESGATE DA IA (mockup docs/mockups/resgate_ia.html, aprovado pelo dono em 27/09/2026):
-- o lead que fica 7 dias inteiros sem mensagem nossa passa, no 8º dia, pro membro IA
-- (o "zaq teste" na Prime), que volta a chamar o cliente com as regras dele — preço
-- liberado, visita, orçamento com conferência. O vendedor só segura o lead mandando
-- mensagem ou registrando o motivo no histórico da ficha (`prospeccao_atividades`).
--
-- Medido na Prime (conta 34) em 26/09, só leitura: 326 leads parados há 7 dias ou mais,
-- 317 deles no chip principal, que é QR. Por isso o teto (20 por dia), o espaçamento,
-- o horário e o freio: o chip principal é de onde vem a receita (CLAUDE.md §0).
--
-- 1. `resgate_config`: uma linha por empresa — modo, prazo, teto, horário, supervisor.
-- 2. `resgate_leads`: um por lead que passou pra IA (de quem era, quando, o estado).
-- 3. `resgate_envios`: tudo que o resgate mandou — é o relógio do teto e do
--    espaçamento, o dedup do aviso ao vendedor e o material do resumo das 19h.
-- 4. `resgate_teste`: a conversa do "Testar comigo" (o supervisor faz o papel do
--    cliente; nada disso vira lead nem mensagem da empresa).
--
-- Não toca em `canais_config` nem em nada da conexão (CLAUDE.md §1). Tabelas à parte,
-- sem coluna nova em tabela viva. Aditiva e idempotente.

create table if not exists public.resgate_config (
  conta_id            bigint primary key references public.contas(id) on delete cascade,
  -- off: nada roda · ensaio: só prévias pro supervisor, nenhum lead muda de dono ·
  -- ligado: o lead passa pra IA e a mensagem sai pro cliente
  modo                text     not null default 'off',
  membro_id           bigint   references public.membros(id) on delete set null,
  dias                smallint not null default 7,
  -- o lead que o vendedor já esquentou (visita ou orçamento): quantos dias de silêncio
  -- até ir pro resgate. Nulo = nunca vai (fica com o vendedor e o follow-up dele)
  aquecido_dias       smallint default 14,
  teto_dia            smallint not null default 20,
  hora_ini            smallint not null default 9,
  hora_fim            smallint not null default 19,
  dias_semana         smallint[] not null default '{0,1,2,3,4,5}',   -- seg=0 … dom=6
  supervisor_whatsapp text,
  incluir_perdidos    boolean  not null default true,
  aviso_vendedor      boolean  not null default true,
  -- o freio: pausado sozinho (chip caiu não pausa; pedido de parar e falha de envio sim)
  pausado_em          timestamptz,
  pausado_motivo      text,
  ligado_em           timestamptz,
  atualizado_em       timestamptz not null default now(),
  constraint resgate_config_modo_check check (modo in ('off','ensaio','ligado'))
);

create table if not exists public.resgate_leads (
  prospeccao_id   bigint primary key,
  conta_id        bigint not null references public.contas(id) on delete restrict,
  membro_id       bigint,             -- o membro IA que recebeu
  vendedor_antes  bigint,             -- de quem era (nulo = estava sem dono)
  conversa_id     bigint,
  faixa           smallint,           -- 1 cliente esperando · 2 festa por vir · 3 aberto · 4 perdido
  status_antes    text,
  entrou_em       timestamptz not null default now(),
  ativo           boolean not null default true,
  -- chamado → respondeu | parou (pediu pra parar) | pausado (alguém da equipe falou)
  -- | devolvido (o gestor deu o lead pra alguém)
  estado          text not null default 'chamado',
  ultimo_envio_em timestamptz,
  respondeu_em    timestamptz,
  opt_out         boolean not null default false,
  saiu_em         timestamptz
);
create index if not exists resgate_leads_conta_idx on public.resgate_leads (conta_id, ativo);

create table if not exists public.resgate_envios (
  id            bigserial primary key,
  conta_id      bigint not null,
  prospeccao_id bigint,
  membro_id     bigint,               -- no aviso ao vendedor, o vendedor
  -- previa (ensaio) · retomada (ligado) · aviso_vendedor · resumo · supervisor · teste
  tipo          text not null,
  ref_em        timestamptz,          -- o fato que gerou (o início do relógio do lead)
  texto         text,
  ok            boolean not null default true,
  erro          text,
  criado_em     timestamptz not null default now()
);
create index if not exists resgate_envios_conta_idx on public.resgate_envios (conta_id, tipo, criado_em);
create index if not exists resgate_envios_lead_idx on public.resgate_envios (prospeccao_id, tipo);

create table if not exists public.resgate_teste (
  id            bigserial primary key,
  conta_id      bigint not null,
  numero8       text not null,        -- os 8 últimos dígitos do supervisor
  prospeccao_id bigint,               -- o lead de verdade que dá o contexto
  historico     jsonb not null default '[]'::jsonb,
  criado_em     timestamptz not null default now(),
  expira_em     timestamptz not null default now() + interval '2 hours'
);
create index if not exists resgate_teste_conta_idx on public.resgate_teste (conta_id, numero8, expira_em);

-- rollback:
--   drop table if exists public.resgate_teste;
--   drop table if exists public.resgate_envios;
--   drop table if exists public.resgate_leads;
--   drop table if exists public.resgate_config;
