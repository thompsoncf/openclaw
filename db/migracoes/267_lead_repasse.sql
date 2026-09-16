-- 267_lead_repasse.sql
-- Quem passou o lead pra quem, quando e por quê.
--
-- O PEDIDO (dono, 16/09/2026): "preciso que com o próprio vendedor mude de quem é
-- quem, porque existem leads que já foram atendidos no passado e eles precisam se
-- entender".
--
-- O QUE FALTAVA. Até aqui só o dono trocava, e a troca NÃO DEIXAVA RASTRO:
-- `prospeccao.vendedor_id` era sobrescrito e nenhuma tabela guardava quem tinha
-- antes. `funil_movimentos` registra movimento de ETAPA, não de dono.
--
-- É esse rastro que falta pra "eles se entenderem": sem histórico ninguém prova
-- quem atendeu primeiro. E foi por isso que, ao medir o pedido, eu não consegui
-- dizer quantas vezes isso já tinha acontecido — o dado nunca foi guardado.
--
-- COMO O PROBLEMA NASCE. `distribuicao.atribuir_se_sem_dono` nunca rouba lead com
-- dono. Mas quando o mesmo número escreve de novo, nasce um lead NOVO, sem dono, e
-- o rodízio entrega pro próximo da fila. O contato é o mesmo; a linha na tabela,
-- não. Caso real na conta 34: Lêda Lopes, 20/08 com a Jacqueline e 24/08 com o
-- Thiago, mesmo número.
--
-- `de_membro_id` É NULO quando o lead não tinha dono (veio órfão). Nulo aqui quer
-- dizer "não era de ninguém", e é diferente de não ter linha — que quer dizer
-- "nunca foi passado".
--
-- SEM FOREIGN KEY pros membros de propósito: desativar alguém da equipe não pode
-- apagar o histórico de que ele atendeu, e é justamente o vendedor que SAIU o que
-- mais gera a pergunta "quem cuidava disso?".
--
-- Aditivo e idempotente.

create table if not exists public.lead_repasse (
  id              bigserial primary key,
  conta_id        bigint not null,
  prospeccao_id   bigint not null,
  de_membro_id    bigint,          -- nulo = estava sem dono
  para_membro_id  bigint not null,
  por_membro_id   bigint,          -- quem apertou o botão (nulo = rodízio/sistema)
  motivo          text,
  criado_em       timestamptz not null default now(),
  -- quando quem RECEBEU abriu o lead. É o que faz o aviso "fulano te passou a
  -- Lêda" sair da Fila dele sozinho: passar sem avisar seria o lead sumir da tela
  -- de um e aparecer na do outro, calado — e quem recebe precisa saber por quê.
  visto_em        timestamptz
);

-- "o que já aconteceu com ESTE lead", que é a leitura da ficha
create index if not exists idx_lead_repasse_lead
    on public.lead_repasse (prospeccao_id, id desc);

-- "o que andou nesta conta", pro dono conferir quando alguém questiona
create index if not exists idx_lead_repasse_conta
    on public.lead_repasse (conta_id, criado_em desc);

-- "o que me passaram e eu ainda não vi", que é o aviso na Fila de quem recebeu
create index if not exists idx_lead_repasse_recebi
    on public.lead_repasse (para_membro_id, criado_em desc)
 where visto_em is null;

-- rollback:
--   drop table if exists public.lead_repasse;
