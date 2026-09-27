-- A festa aconteceu? (revisão do funil, parte 2, item 7 — aprovado em 27/09/2026).
--
-- `festa_confirmacao`: uma linha por festa de um card (o lead e a data da festa). As
-- perguntas à equipe (9h e 18h do dia seguinte) e a resposta: 'aconteceu' (o card vai
-- pro Pós-festa e o agradecimento sai), 'remarcou' ou 'cancelou' (o card fica). O
-- gatilho `festa_passou` passa a exigir o 'aconteceu' (finance/festa_aconteceu.py).
--
-- Não toca conversa, mensagem, conexão, chip nem `canais_config` (CLAUDE.md §0/§1),
-- nem card nenhum. Aditiva e idempotente.

create table if not exists public.festa_confirmacao (
  prospeccao_id  bigint not null,
  conta_id       bigint not null,
  evento_em      date   not null,
  pergunta_1_em  timestamptz,
  pergunta_2_em  timestamptz,
  resposta       text check (resposta in ('aconteceu','remarcou','cancelou')),
  respondido_em  timestamptz,
  membro_id      bigint,
  criado_em      timestamptz not null default now(),
  primary key (prospeccao_id, evento_em)
);

create index if not exists festa_confirmacao_conta_idx
  on public.festa_confirmacao (conta_id) where resposta is null;
