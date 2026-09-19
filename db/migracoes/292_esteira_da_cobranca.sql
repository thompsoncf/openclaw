-- 292_esteira_da_cobranca.sql
-- A ESTEIRA: 10 leads por dia, 3 cobranças em 7 dias, e o vendedor decide.
--
-- Desenho do dono em 19/09/2026, depois de ver por que a cobrança de ontem não
-- funcionou. As palavras dele:
--
--   "como tem muito lead a muito tempo parado o ideal e comecar a cobranca depois
--    do aviso ... cobrar os 10 leads do dia e todo dia manda o resumo do que ele
--    fez ou nao fez e no outro dia manda mais 10. ele e que tem que escrever o
--    historico e colocar como perdido ... tem 7 dias de prazo final"
--
-- POR QUE UMA TABELA, E NÃO MAIS UMA CONTA EM CIMA DO PRAZO
-- A cobrança antiga contava a partir do prazo VENCIDO da etapa. Medido na conta 34
-- em 19/09: a Layane recebeu os quatro degraus da escada (venc, 24h, 48h, 72h) no
-- MESMO MINUTO, porque o prazo dela venceu em 14/08 — um mês antes. O lead nascia
-- com as três chances já gastas. Não havia como dar chance nenhuma sem um marco
-- novo: a hora em que ESTE lead entrou na esteira. É o que esta tabela guarda.
--
-- `entrou_em` é o único relógio da esteira. Os dias de cobrança (1, 3 e 7, de
-- `fu_toques_dias`) contam a partir dele, e o prazo final é entrou_em + 7 dias.
--
-- UMA ESTEIRA ATIVA POR LEAD. O índice único parcial é o que impede o mesmo lead
-- de entrar duas vezes e ser cobrado em dobro — e é também o que faz `entrar()`
-- poder rodar a cada ciclo do poller sem contar quantas vezes rodou.
--
-- `resolvido_em` é preenchido quando o vendedor AGE: falou com o cliente (inclusive
-- pelo WhatsApp Web, que é como a Prime trabalha — 1.191 das 1.505 mensagens de
-- setembro saíram de lá), moveu o card, ou fechou com histórico. `fechado_em` é o
-- contrário: o dia 7 chegou e ninguém tratou.
--
-- NASCE DESLIGADA. `esteira_modo` é 'off' em toda conta.
--
-- Aditiva e idempotente.

create table if not exists public.follow_up_esteira (
  id             bigserial primary key,
  conta_id       bigint      not null,
  prospeccao_id  bigint      not null,
  membro_id      bigint,
  etapa          text        not null default '',
  entrou_em      timestamptz not null default now(),
  -- o vendedor agiu: 'falou' | 'moveu' | 'fechou' | 'cliente_voltou'
  resolvido_em   timestamptz,
  resolucao      text,
  -- o dia 7 passou e ninguém tratou
  fechado_em     timestamptz,
  criado_em      timestamptz not null default now()
);

-- UMA esteira ativa por lead. Sem isto, `entrar()` duplicaria o lead a cada ciclo.
create unique index if not exists ux_esteira_ativa
  on public.follow_up_esteira (conta_id, prospeccao_id)
  where resolvido_em is null and fechado_em is null;

-- a passada diária pergunta sempre "quem desta conta está na esteira"
create index if not exists ix_esteira_conta_entrada
  on public.follow_up_esteira (conta_id, entrou_em desc);

alter table public.follow_up_esteira
  drop constraint if exists follow_up_esteira_resolucao_ck;
alter table public.follow_up_esteira
  add constraint follow_up_esteira_resolucao_ck
  check (resolucao is null or resolucao in ('falou','moveu','fechou','cliente_voltou'));

alter table public.funil_regua
  add column if not exists esteira_modo text not null default 'off';

alter table public.funil_regua
  drop constraint if exists funil_regua_esteira_modo_ck;
alter table public.funil_regua
  add constraint funil_regua_esteira_modo_ck
  check (esteira_modo in ('off', 'observando', 'ligado'));

-- rollback:
--   alter table public.funil_regua drop column if exists esteira_modo;
--   drop table if exists public.follow_up_esteira;
