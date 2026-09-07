-- 218_follow_up.sql
-- O follow-up automático: a próxima ação de cada lead, e a história de quem adiou.
--
-- O QUE MOTIVOU (medido na conta 34, PRIME EVENTOS, em 07/09/2026)
-- O pedido do dono era um painel de follow-up com "próxima ação", "data/hora" e
-- "responsável" em todo lead ativo. Os campos já existiam. Preenchidos:
--
--     proximo_contato_em ....... 1 lead de 274 ativos
--     ultimo_contato_em ........ 30 de 297
--
-- Uma régua que COBRASSE o preenchimento abriria o dia 1 com 273 leads em "sem
-- próxima ação" — e a meta do dono ("sem próxima ação = zero") viraria 273
-- preenchimentos na mão pra três vendedores. A equipe desliga na primeira manhã.
--
-- Por isso a próxima ação NASCE SOZINHA, da escada abaixo, e o vendedor só
-- CORRIGE. O campo em branco deixa de existir: com a escada, "sem próxima ação"
-- cai de 273 pra 4 — só os leads cuja festa já passou.
--
-- POR QUE O RELÓGIO NÃO É SÓ 24/48/72h
-- Em eventos a data da festa é um segundo relógio: o casamento de 19/09 e a festa
-- de junho/2027 não podem ter o mesmo prazo. Na Prime são 102 leads ativos com
-- data futura e 7 nos próximos 30 dias. O prazo é o MAIS APERTADO dos dois — e
-- pra quem não vende festa (perfil recorrente) o segundo relógio simplesmente não
-- existe (CLAUDE.md §6: toda tela segue o nicho).
--
-- A TABELA NOVA É A DO ADIAMENTO, NÃO A DO PRAZO
-- O prazo vigente mora em `prospeccao.proximo_contato_em`, que já existe. O que o
-- banco nunca teve é a HISTÓRIA: quem adiou, pra quando, por quê, e quantas vezes
-- seguidas sem falar com o cliente. Sem ela, o vendedor empurra a data toda vez
-- que o aviso chega e o painel fica verde sem ninguém ter conversado com ninguém.
-- Nada aqui é sobrescrito: cada marcação é uma linha nova.
--
-- NASCE DESLIGADO. `follow_up_modo` começa 'off' em toda conta. Em 'observando' o
-- motor calcula e grava o que TERIA avisado, sem mandar push nenhum — é assim que
-- se vê o volume real antes de alguém receber o primeiro aviso.

-- ---------------------------------------------------------------- 1. a história
create table if not exists public.follow_up_marcacoes (
  id            bigserial primary key,
  conta_id      bigint not null,
  prospeccao_id bigint not null,
  -- pra quando ficou o compromisso
  prazo_em      timestamptz not null,
  -- o que se combinou fazer ("mandar proposta", "segundo toque"…)
  acao          text not null default '',
  -- quem marcou. NULL = o sistema (a escada). Preenchido = a mão do vendedor,
  -- e é essa distinção que separa "o sistema propôs" de "alguém adiou".
  membro_id     bigint,
  automatico    boolean not null default false,
  motivo        text not null default '',
  criado_em     timestamptz not null default now()
);
create index if not exists idx_fu_marc_lead on public.follow_up_marcacoes (prospeccao_id, criado_em desc);
create index if not exists idx_fu_marc_conta on public.follow_up_marcacoes (conta_id, criado_em desc);

-- ---------------------------------------------------------------- 2. a config
alter table public.funil_regua
  -- off | observando | ligado — o mesmo trio dos outros dois modos da régua
  add column if not exists follow_up_modo   text    not null default 'off',
  -- a escada, em dias. Os números saíram do que a Prime já pratica, não de manual:
  -- proposta parada 3 dias (a mediana de retorno de proposta), e os toques em
  -- 2/4/7/15 — a partir do 4º a tela pergunta se encerra em vez de insistir.
  add column if not exists fu_proposta_dias integer not null default 3,
  add column if not exists fu_toques_dias   text    not null default '2,4,7,15',
  -- festa em até N dias sem proposta vence HOJE. Só o perfil de eventos usa.
  add column if not exists fu_festa_dias    integer not null default 30,
  -- teto de leads cobrados por vendedor por dia. 15 = o tamanho da fila do dia;
  -- o aviso sai AGRUPADO ("7 leads esperando você" é um push, não sete).
  -- O teto_avisos_dia da cobrança continua sendo o dele, e são somados: o
  -- vendedor tem um orçamento de atenção por dia, não um por motor.
  add column if not exists fu_teto_dia      integer not null default 15;

alter table public.funil_regua drop constraint if exists funil_regua_follow_up_modo_check;
alter table public.funil_regua
  add constraint funil_regua_follow_up_modo_check
  check (follow_up_modo in ('off', 'observando', 'ligado'));
