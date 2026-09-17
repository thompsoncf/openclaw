-- 276_aviso_envios.sql
-- O registro de QUE aviso saiu pra QUEM, e se saiu.
--
-- POR QUE ESTA TABELA EXISTE. Em 17/09/2026, no primeiro dia do follow-up ligado
-- de verdade, o dono perguntou: "já mandou os e-mails pros vendedores? me traz os
-- logs". E eu não tinha resposta. Dava pra provar o que foi COBRADO
-- (`funil_avisos`, 129 linhas às 08:58) e não o que SAIU: o `enviar_email` escreve
-- só no log da aplicação, o `enviar_push` devolve um número que ninguém guardava, e
-- o `notificar` engole toda exceção pra não derrubar o poller.
--
-- Resultado: quando um vendedor diz "não recebi", a discussão é sem prova. O
-- WhatsApp de saída já tinha `envio_falha` desde 16/09; o aviso interno não tinha
-- nada.
--
-- O DESENHO segue o `resumo_semanal_envio` (migração 274), que resolveu o mesmo
-- problema pro relatório semanal — mesma ideia de `destino`/`ok`/`motivo`. A
-- diferença é que aqui cabe MAIS DE UM CANAL pro mesmo aviso (o follow-up manda
-- push e e-mail juntos, e vai mandar WhatsApp), então a linha é por CANAL e não
-- por pessoa.
--
-- NÃO TEM CHAVE ÚNICA, de propósito. O mesmo vendedor recebe o mesmo tipo de aviso
-- todo dia, e o dedup de verdade já é feito por quem cobra (`funil_avisos`, por
-- FATO). Uma unique aqui transformaria "mandei de novo amanhã" em conflito
-- silencioso — e esta tabela existe justamente pra registrar, nunca pra barrar.
--
-- `destino` guarda o e-mail ou o número inteiro. É dado da própria equipe, visível
-- só pra dono e gestor da conta, e sem ele o registro não serve: "falhou pra um
-- vendedor" não diz pra qual endereço.
--
-- Aditivo e idempotente.

create table if not exists public.aviso_envios (
  id          bigserial primary key,
  conta_id    bigint not null,
  membro_id   bigint,                    -- nulo = destino sem membro (e-mail solto)
  origem      text   not null,           -- 'follow_up' | 'distribuicao' | ...
  canal       text   not null,           -- 'email' | 'push' | 'whatsapp'
  destino     text,                      -- e-mail ou número; nulo no push
  assunto     text,
  n_leads     int,                       -- quantos leads o aviso agrupou
  ok          boolean not null,
  motivo      text,                      -- por que não saiu (erro, sem número…)
  criado_em   timestamptz not null default now()
);

-- a consulta da tela: "o que saiu nesta conta, do mais novo pro mais velho"
create index if not exists idx_aviso_envios_conta
    on public.aviso_envios (conta_id, criado_em desc);

-- e a do suporte: "o Thiago recebeu?"
create index if not exists idx_aviso_envios_membro
    on public.aviso_envios (conta_id, membro_id, criado_em desc);

-- só o que FALHOU, que é a pergunta urgente. Parcial porque a falha é a minoria —
-- num índice cheio ela se perde entre milhares de sucessos.
create index if not exists idx_aviso_envios_falha
    on public.aviso_envios (conta_id, criado_em desc) where not ok;

-- rollback:
--   drop table if exists public.aviso_envios;
