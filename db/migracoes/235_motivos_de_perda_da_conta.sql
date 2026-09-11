-- 235_motivos_de_perda_da_conta.sql
-- Por que o lead foi perdido: a lista deixa de ser constante do código e vira lista
-- DA CONTA, e a perda passa a guardar o que o dono pediu. Regra 5 do
-- FLUXO_FINAL_FUNIL_PRIME_EVENTOS_DESENVOLVEDOR_V3.
--
-- O QUE O DONO PEDIU
--   "A coluna PERDIDO deve receber somente leads que encerraram efetivamente o ciclo
--    comercial, evitando que o vendedor use essa etapa apenas para 'limpar' o funil.
--    Ao marcar como PERDIDO, o sistema deve exigir obrigatoriamente o motivo da
--    perda, com opções padronizadas. (...) Além do motivo, o CRM deve salvar
--    automaticamente vendedor responsável, data da perda, etapa de origem, tempo
--    total no funil e histórico completo das conversas e tentativas."
--
-- POR QUE A LISTA VIRA TABELA
-- Ela era sete chaves fixas no código (`raio_x_perfil.MOTIVOS_TODOS`) com um CHECK no
-- banco. Os dez do documento são os DA PRIME: a próxima empresa de eventos vai querer
-- "buffet próprio", "estacionamento", "não aceitou o regulamento" — e nenhum desses
-- pode depender de um deploy. Pedido do dono em 11/09/2026: "serve pra outras
-- empresas do mesmo nicho ou outras, sempre tem que pensar dessa forma".
--
-- Cada conta recebe a lista do PERFIL DELA na primeira vez que abrir a tela (o mesmo
-- jeito do `_etapas`), e dali em diante liga, desliga, renomeia, reordena e
-- acrescenta o que for dela. O CHECK sai justamente por isso: um motivo criado pelo
-- dono não tem como estar numa lista escrita meses antes.
--
-- O QUE A PERDA PASSA A GUARDAR
-- `perda_motivo` já existia. Entram `perda_descricao` (o texto obrigatório do
-- "Outro"), `perda_em` (a data da perda) e `perda_etapa` (de onde ele veio). Tempo
-- total no funil NÃO vira coluna: é `perda_em - criado_em`, e guardar derivado é
-- guardar uma segunda verdade que sai de sincronia no primeiro backfill. O histórico
-- completo das conversas e tentativas também já existe — `mensagens`,
-- `funil_movimentos`, `funil_renovacoes` — e nada aqui apaga nenhum deles.
--
-- NASCE DESLIGADO, como o resto. `exige_motivo` é da ETAPA e começa false: o dono
-- liga no Perdido quando quiser, e quem não ligar continua podendo fechar sem
-- motivo, que é como o produto sempre funcionou.

-- ---------------------------------------------------------------- 1. a lista
create table if not exists public.funil_motivos_perda (
  id             bigserial primary key,
  conta_id       bigint not null,
  -- a chave é estável e é o que vai em `prospeccao.perda_motivo`; o rótulo é o que
  -- a tela mostra, e renomear não pode reescrever a história de quem já foi perdido
  chave          text not null,
  rotulo         text not null,
  ordem          integer not null default 0,
  ativo          boolean not null default true,
  -- "Outro — exige descrição obrigatória" é uma propriedade do MOTIVO, não um `if`
  -- com a chave 'outro' dentro: outra empresa pode querer descrição em "estrutura
  -- não atendeu" pra saber o que faltou
  exige_descricao boolean not null default false,
  criado_em      timestamptz not null default now()
);
create unique index if not exists uq_fmp_conta_chave
  on public.funil_motivos_perda (conta_id, chave);
create index if not exists idx_fmp_conta on public.funil_motivos_perda (conta_id, ordem);

-- ---------------------------------------------------------------- 2. a perda
alter table public.prospeccao
  add column if not exists perda_descricao text,
  add column if not exists perda_em        timestamptz,
  add column if not exists perda_etapa     text;

comment on column public.prospeccao.perda_descricao is
  'texto livre do motivo que exige descrição; o motivo em si fica em perda_motivo';
comment on column public.prospeccao.perda_etapa is
  'a etapa de onde o lead saiu ao ser perdido — "etapa de origem" do pedido do dono';

-- O CHECK SAI. Ele listava sete chaves escritas no código; com a lista por conta,
-- um motivo que o dono criar hoje não estaria lá e o INSERT quebraria na cara dele.
-- Quem valida passa a ser a aplicação, contra a lista DA CONTA — que é a única que
-- sabe o que é válido ali. Nenhuma linha existente muda.
alter table public.prospeccao drop constraint if exists prospeccao_perda_motivo_check;

-- ---------------------------------------------------------------- 3. a exigência
alter table public.funil_etapas
  -- qual etapa exige motivo pra receber um lead. Nasce false: o dono liga no
  -- Perdido quando quiser, e não existe "a etapa Perdido" fixa no código — outra
  -- empresa pode exigir motivo ao arquivar, ao pausar, ao que for.
  add column if not exists exige_motivo boolean not null default false;

comment on column public.funil_etapas.exige_motivo is
  'mover um lead PARA esta etapa exige motivo da lista da conta; false = como sempre foi';

-- rollback:
--   alter table public.funil_etapas drop column exige_motivo;
--   alter table public.prospeccao drop column perda_descricao, drop column perda_em,
--     drop column perda_etapa;
--   drop table public.funil_motivos_perda;
--   (o CHECK da 213 pode voltar, se nenhuma conta tiver criado motivo próprio)
