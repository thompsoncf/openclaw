-- 154_render_evento.sql
-- Histórico de deploys e incidentes do Render, no NOSSO banco.
--
-- Por que guardar isso aqui, se o Render já tem um painel? Porque o painel só
-- responde a quem está logado nele, com sessão de navegador. O agente (e o
-- monitor_saldos, e qualquer script) não tem essa sessão — e, no ambiente do
-- Claude Code na web, `api.render.com` cai em 403 na política de egresso. Sem
-- esta tabela, a única forma de saber que um deploy quebrou é alguém abrir o
-- dashboard e olhar. Com ela, o histórico fica a um SELECT de distância, no
-- mesmo Postgres que o resto do sistema já usa.
--
-- Quem popula é o receptor do webhook (POST /webhook/render), que o Render
-- chama a cada evento. O corpo que o Render manda é MAGRO (tipo, serviceId e o
-- id do evento), então o receptor enriquece via API do Render — de dentro do
-- Render, onde a API é alcançável — e grava o resultado já mastigado aqui.
--
-- IDEMPOTÊNCIA: `webhook_id` é o header `webhook-id` do padrão Standard
-- Webhooks, único por entrega. O Render REENTREGA o webhook quando não recebe
-- 200 a tempo, então sem essa trava um deploy que demora pra responder viraria
-- 3 linhas e 3 alertas. O unique index resolve com `on conflict do nothing`.
--
-- `payload` e `detalhes` ficam em jsonb de propósito: o Render adiciona campos
-- novos aos eventos sem avisar, e assim a gente não perde o que ainda não
-- mapeou em coluna — dá pra consultar depois sem migração nova.
--
-- Aditivo e idempotente.

create table if not exists public.render_evento (
    id            bigserial primary key,
    -- entrega (header webhook-id): trava de idempotência
    webhook_id    text not null,
    -- id do evento no Render (payload.data.id); serve pra buscar detalhes na API
    evento_id     text,
    tipo          text not null,          -- deploy_ended, deploy_started, server_failed...
    servico_id    text,                   -- srv-xxxx
    servico_nome  text,                   -- resolvido via API (openclaw-web-bcu3)
    deploy_id     text,                   -- dep-xxxx
    status        text,                   -- live, build_failed, update_failed, canceled...
    status_num    integer,                -- details.status cru do evento (2 = sucesso)
    sucesso       boolean,                -- null = não se aplica / desconhecido
    commit_id     text,
    commit_msg    text,
    -- cauda de log capturada no momento da falha (só em falha, pra não inchar)
    log_trecho    text,
    ocorrido_em   timestamptz,            -- timestamp que o Render mandou
    recebido_em   timestamptz not null default now(),
    payload       jsonb,                  -- corpo cru do webhook
    detalhes      jsonb                   -- resposta do /v1/events/{id}
);

-- Idempotência da entrega. Unique index (e não constraint na coluna) porque o
-- `on conflict (webhook_id) do nothing` do receptor precisa de um índice único.
create unique index if not exists render_evento_webhook_id_uidx
    on public.render_evento (webhook_id);

-- Consulta mais comum de longe: "o que aconteceu por último neste serviço?".
create index if not exists render_evento_servico_recente_idx
    on public.render_evento (servico_id, recebido_em desc);

-- Segunda consulta mais comum: "me mostra só o que quebrou".
-- Índice PARCIAL: só indexa as falhas, que são a minoria — fica pequeno e
-- rápido, sem pesar nos inserts de deploy que deu certo.
create index if not exists render_evento_falha_idx
    on public.render_evento (recebido_em desc)
    where sucesso is false;

-- rollback:
--   drop index if exists public.render_evento_falha_idx;
--   drop index if exists public.render_evento_servico_recente_idx;
--   drop index if exists public.render_evento_webhook_id_uidx;
--   drop table if exists public.render_evento;
