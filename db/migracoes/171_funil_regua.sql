-- 171_funil_regua.sql
-- A régua do funil: fase na etapa, prazo, gatilho — e o histórico que nunca existiu.
--
-- O QUE MOTIVOU
-- Medido na conta 34 (PRIME EVENTOS) em 18/08/2026: 87 leads no funil, 81 parados
-- em "Novo". A leitura óbvia — "ninguém trabalha os leads" — está ERRADA: 74 desses
-- 81 JÁ tinham resposta nossa na conversa. Os vendedores atendem (mediana de
-- primeira resposta: 12 a 18 minutos, medido em 610 mensagens de 21 dias). O que
-- não acontece é alguém arrastar o card. O funil não está abandonado — está
-- mentindo, e todo relatório tirado dele mente junto.
--
-- Só que "registrar atividade" ninguém usa: 5 atividades em toda a história da
-- conta. Qualquer régua que meça atividade REGISTRADA dispararia 79 alarmes falsos
-- no primeiro minuto. Por isso o relógio desta régua lê a CONVERSA (mensagens
-- in/out, incluindo o eco fromMe do WhatsApp, que grava o que o vendedor responde
-- pelo próprio celular) — não o que alguém lembrou de clicar.
--
-- AS TRÊS COISAS QUE ESTA MIGRAÇÃO CRIA
--
--  1. FASE NA ETAPA. Hoje `ganho` é o fim da linha: etapa nova sempre nasce com
--     ordem < 900 (painel_prospeccao._ORDEM_GANHO), então NENHUMA coluna pode vir
--     depois do fechamento. Para uma empresa de eventos isso é errado na origem —
--     o evento acontece DEPOIS de o sinal ser pago. A conta 34 já criou
--     "Eventos Realizados" e ela está encalhada entre Proposta e Sinal Pago.
--     Com `fase`, "fechado" deixa de ser `status='ganho'` cru e passa a ser
--     "fase in ('fechamento','pos') e chave <> 'perdido'" — uma pergunta só, que
--     relatório, comissão e cockpit passam a fazer no mesmo lugar.
--
--  2. HISTÓRICO DE MOVIMENTO. O banco NUNCA guardou quando um card muda de coluna.
--     Ninguém consegue responder "quanto tempo um lead fica em Proposta" — nem
--     hoje, nem olhando pra trás. Sem isso não há como escolher prazo com dado, só
--     com palpite. `funil_movimentos` começa a gravar desde já, inclusive com a
--     régua desligada: anotar o que humanos já fazem não é automação, não incomoda
--     ninguém, e é o que faz as duas semanas de espera valerem alguma coisa.
--
--  3. A CONFIG, NASCENDO DESLIGADA. `funil_regua` guarda modo, janela de
--     atendimento, prazos e teto. Os dois modos nascem 'off' e cada etapa nasce com
--     `gatilho_ativo=false`: o dono liga um gatilho de cada vez, quando quiser.
--     Nada aqui manda mensagem pra ninguém no dia do deploy — de propósito.
--
-- POR QUE `prazo_min` EM MINUTOS
-- Prazo é regulável na tela (número + unidade), e a tela mostra "4 horas" ou
-- "3 dias" a gosto. No banco fica uma unidade só, senão comparar etapa com etapa
-- vira conversão em toda consulta. NULL = etapa sem prazo (não cobra).

-- ---------------------------------------------------------------- 1. a etapa
alter table public.funil_etapas
  add column if not exists fase text not null default 'venda',
  add column if not exists prazo_min integer,
  add column if not exists gatilho text,
  add column if not exists gatilho_ativo boolean not null default false;

alter table public.funil_etapas
  drop constraint if exists funil_etapas_fase_check;
alter table public.funil_etapas
  add constraint funil_etapas_fase_check check (fase in ('venda', 'fechamento', 'pos'));

-- As duas fixas de resultado JÁ SÃO fechamento — só nunca tiveram como dizer isso.
-- Sem este backfill, o dia em que o relatório passar a contar por fase ele conta
-- zero venda ganha, porque toda etapa nasceu 'venda' pelo default acima.
update public.funil_etapas set fase = 'fechamento' where chave in ('ganho', 'perdido');

comment on column public.funil_etapas.fase is
  'venda | fechamento | pos — "fechado" é fase in (fechamento,pos) e chave <> perdido';
comment on column public.funil_etapas.prazo_min is
  'minutos de ATENDIMENTO que um lead pode ficar nesta etapa; null = sem prazo';
comment on column public.funil_etapas.gatilho is
  'chave do evento que traz o lead pra cá sozinho; null = só na mão';

-- ---------------------------------------------------------------- 2. o histórico
create table if not exists public.funil_movimentos (
  id           bigserial primary key,
  conta_id     bigint not null,
  prospeccao_id bigint not null,
  de           text,
  para         text not null,
  -- 'manual' | 'gatilho:<evento>' | 'simulado:<evento>' — o simulado é o modo
  -- observação: anota o salto que TERIA acontecido, sem mexer no card.
  motivo       text not null,
  membro_id    bigint,
  criado_em    timestamptz not null default now()
);

create index if not exists idx_funil_mov_lead
  on public.funil_movimentos (prospeccao_id, criado_em desc);
create index if not exists idx_funil_mov_conta
  on public.funil_movimentos (conta_id, criado_em desc);

-- ---------------------------------------------------------------- 3. o aviso
-- Dedup por (lead, estado, nível, REFERÊNCIA). A referência é o instante do fato
-- que ligou o cronômetro — a mensagem do cliente que ficou sem resposta, por
-- exemplo. Sem ela, "um aviso por lead por nível" seria PARA SEMPRE: o cliente
-- volta a escrever daqui a um mês, fica sem resposta de novo, e ninguém é avisado
-- porque já houve um aviso em agosto. Com ela, fato novo = cronômetro novo.
create table if not exists public.funil_avisos (
  id            bigserial primary key,
  conta_id      bigint not null,
  prospeccao_id bigint not null,
  estado        text not null,   -- sem_resposta | bola_nossa | bola_cliente | etapa
  nivel         text not null,   -- vendedor | gestor
  etapa         text not null default '',
  ref_em        timestamptz not null,
  simulado      boolean not null default false,
  membro_id     bigint,
  criado_em     timestamptz not null default now()
);

create unique index if not exists uq_funil_aviso
  on public.funil_avisos (prospeccao_id, estado, nivel, etapa, ref_em, simulado);
create index if not exists idx_funil_aviso_conta
  on public.funil_avisos (conta_id, criado_em desc);

-- ---------------------------------------------------------------- 4. a config
create table if not exists public.funil_regua (
  conta_id          bigint primary key,
  -- off | observando | ligado. NASCEM OFF: o dono pediu pra configurar gatilho a
  -- gatilho antes de qualquer coisa agir. 'observando' roda o motor inteiro e
  -- grava o que teria feito, sem mover card nem avisar ninguém.
  gatilhos_modo     text not null default 'off',
  cobranca_modo     text not null default 'off',
  -- dias da semana ISO (1=segunda … 7=domingo). Eventos é negócio de sábado.
  janela_dias       text not null default '1,2,3,4,5,6',
  janela_abre       time not null default '08:00',
  janela_fecha      time not null default '19:00',
  -- prazos da conversa, em minutos de atendimento
  sem_resposta_min  integer not null default 120,
  bola_nossa_min    integer not null default 240,
  bola_cliente_min  integer not null default 4320,
  escala_min        integer not null default 240,
  teto_avisos_dia   integer not null default 5,
  criado_em         timestamptz not null default now(),
  atualizado_em     timestamptz not null default now()
);

alter table public.funil_regua drop constraint if exists funil_regua_modos_check;
alter table public.funil_regua add constraint funil_regua_modos_check
  check (gatilhos_modo in ('off', 'observando', 'ligado')
     and cobranca_modo in ('off', 'observando', 'ligado'));
