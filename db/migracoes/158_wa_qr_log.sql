-- 158_wa_qr_log.sql
-- O log do serviço de WhatsApp (services/wa-qr) no NOSSO banco, e o estado vivo
-- de cada sessão.
--
-- POR QUE
-- Num chamado real a sessão de uma cliente ficou MUDA por horas: o painel dizia
-- "conectado" e nada entrava. Deu pra provar o que aconteceu — mensagens,
-- contatos, ecos e mapa de @lid daquela conta param todos no mesmo minuto,
-- enquanto as contas vizinhas seguiam gravando — mas não deu pra saber POR QUÊ,
-- porque o motivo estava só no log do Render. E o log do Render não se lê de
-- fora: o dashboard exige sessão de navegador e `api.render.com` cai em 403 na
-- política de egresso do ambiente do agente. A observabilidade de deploy (tabela
-- render_evento, migração 154) só captura cauda de log quando o deploy FALHA —
-- e aqui os deploys passaram; quem quebrou foi a sessão, depois.
--
-- Resultado prático: cada rodada de diagnóstico virava "abra o dashboard, filtre
-- por conta e me diga o que aparece". Estas duas tabelas põem o log a um SELECT
-- de distância, no mesmo Postgres que todo o resto já usa.
--
-- wa_qr_log — as linhas que o serviço decidiu registrar (nível + mensagem +
-- contexto em jsonb). NÃO é o firehose do Baileys: quem escreve aqui é o log
-- da nossa aplicação, que já é curado. Retenção curta (o serviço apaga o que
-- passa de 48h): isto é ferramenta de diagnóstico, não arquivo histórico.
--
-- wa_qr_sessao_estado — UMA linha por conta, sobrescrita: o que a sessão diz de si
-- mesma agora (status, quando entregou evento pela última vez, há quanto tempo
-- está calada). É o que responde "o serviço acha que está conectado?" sem
-- depender de log nenhum — exatamente a pergunta que ficou sem resposta.
--
-- Aditivo e idempotente.

create table if not exists public.wa_qr_log (
    id        bigserial primary key,
    -- null = linha do serviço, não de uma conta específica (arranque, memória)
    conta_id  bigint,
    nivel     text not null default 'info',
    msg       text not null default '',
    -- o resto do objeto que foi logado; o Baileys e o nosso código põem campos
    -- novos sem avisar, e em jsonb nada se perde por falta de coluna
    dados     jsonb,
    criado_em timestamptz not null default now()
);

-- a consulta do diagnóstico é sempre "as últimas linhas desta conta"...
create index if not exists idx_wa_qr_log_conta on public.wa_qr_log (conta_id, id desc);
-- ...e a limpeza por idade precisa varrer por tempo
create index if not exists idx_wa_qr_log_criado on public.wa_qr_log (criado_em);

create table if not exists public.wa_qr_sessao_estado (
    conta_id       bigint primary key,
    -- conectado | aguardando_qr | reconectando | desconectado
    status         text not null default '',
    -- última vez que ESTE socket entregou alguma coisa (mensagem, recibo,
    -- contato, onda de histórico). É o carimbo que o vigia usa.
    ultimo_evento  timestamptz,
    -- há quanto tempo está calada, em segundos, no momento da escrita. Redundante
    -- com o carimbo de propósito: é o número que se lê sem fazer conta.
    mudo_s         bigint,
    -- quantos religamentos seguidos não trouxeram evento nenhum de volta
    religamentos   integer not null default 0,
    detalhe        jsonb,
    atualizado     timestamptz not null default now()
);
