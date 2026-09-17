-- 272_novidade_semana_vista.sql
-- "Esta semana eu já vi": o ✕ da faixa, agora que a faixa é semanal.
--
-- POR QUE EXISTE (17/09/2026). A faixa da Fila passou a mostrar a SEMANA em vez de
-- um aviso por vez — escolha do dono ("um resumo por semana") depois de a medição
-- mostrar 30 avisos em 7 dias, ele com 47 por ler e ZERO lidos, e a vendedora com
-- mais leads com 28 por ler e zero lidos.
--
-- O ✕ PRECISAVA DE UM LUGAR PRÓPRIO, e não podia ser `novidade_lida`. Em
-- 16/09/2026 o dono barrou, com razão, a ideia de o ✕ marcar tudo como lido:
--
--   marcar 26 avisos como lidos DESTRÓI informação — "não lido" é o que a bolinha
--   do Perfil conta, e não existe desmarcar.
--
-- Com a faixa semanal o problema seria o mesmo, e maior: um toque apagaria a
-- semana inteira. Então o ✕ grava AQUI, e só aqui: a faixa daquela semana para de
-- interromper aquela pessoa, e o estado de leitura de cada aviso continua exatamente
-- como estava. A bolinha continua dizendo a verdade, e "Ver" continua sendo o
-- caminho de ler de fato.
--
-- A SEMANA É TEXTO ('2026-W38'), em hora de Brasília (ver `novidades.semana_de`).
-- Chave ISO e não data de início porque é o que agrupa, e porque um aviso publicado
-- às 21h de sexta é da semana da sexta pra quem recebe — mesmo sendo sábado em UTC.
--
-- SEM FOREIGN KEY pra membros, como em `lead_repasse`: desativar alguém não pode
-- derrubar o que ele já tinha dispensado, e a linha é sobre a PESSOA, não sobre o
-- vínculo dela com a conta hoje.
--
-- Aditivo e idempotente.

create table if not exists public.novidade_semana_vista (
  conta_id   bigint not null,
  membro_id  bigint not null,
  semana     text   not null,          -- '2026-W38', hora de Brasília
  visto_em   timestamptz not null default now(),
  primary key (conta_id, membro_id, semana)
);

-- "que semanas esta pessoa já dispensou", que é a leitura da faixa a cada abertura
-- da Fila. A primary key já serve, e é por isso que não há índice a mais aqui.

-- rollback:
--   drop table if exists public.novidade_semana_vista;
