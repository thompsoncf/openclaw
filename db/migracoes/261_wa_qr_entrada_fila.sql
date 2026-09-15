-- 261_wa_qr_entrada_fila.sql
-- Outbox do repasse ao web: a mensagem do cliente é GRAVADA antes de virar rede.
--
-- POR QUE EXISTE, com data e número. Em 15/09/2026, 09:56, a conta 38 foi pareada
-- e o sync de histórico dela despejou ~9.000 requisições no web em dois minutos e
-- meio. O web parou de responder, o Render matou a instância, e nos ~2 minutos de
-- 502 o wa-qr PERDEU TRÊS MENSAGENS DE CLIENTE (contas 23 e 34) e um eco de saída:
-- o repasse era um `fetch` único, sem timeout e sem retentativa, então cada falha
-- virava uma linha `warn` e a mensagem sumia. O WhatsApp já a deu por entregue e
-- não reenvia. As três estavam no celular do vendedor e nunca no painel.
--
-- A vazão do histórico foi consertada em separado (ver o README do serviço), mas
-- isso trata a CAUSA daquele dia. Web fora do ar acontece por outros motivos —
-- todo deploy é uma janela de 502 — e a consequência continuava a mesma.
--
-- COMO FUNCIONA. A mensagem entra aqui antes de qualquer rede e só ganha
-- `entregue_em` quando o web responde 2xx. O que falha fica com a próxima
-- tentativa marcada em `proxima_em`, e um drenador em cada worker volta de 15 em
-- 15s. Nada é apagado enquanto não entregue. `parada_em` é o dead-letter: depois
-- de muitas tentativas a linha sai do caminho (pra não segurar a fila da conta),
-- mas continua aqui pra alguém olhar.
--
-- Reentregar é seguro: o lado Python é idempotente por `provider_sid` nos dois
-- webhooks (`on conflict (conversa_id, provider_sid) do nothing`).
--
-- Aditiva e idempotente.
create table if not exists public.wa_qr_entrada_fila (
    id            bigserial primary key,
    conta_id      bigint      not null,
    -- 'entrada' -> /webhooks/wa-qr · 'saida' -> /webhooks/wa-qr/saida
    rota          text        not null,
    -- id da mensagem no WhatsApp (m.key.id). O Baileys reentrega a mesma mensagem
    -- quando a conexão oscila ('append'); aqui ela nem chega a entrar duas vezes.
    provider_sid  text        not null default '',
    -- o corpo exatamente como vai no POST
    corpo         jsonb       not null,
    tentativas    integer     not null default 0,
    proxima_em    timestamptz not null default now(),
    entregue_em   timestamptz,
    parada_em     timestamptz,
    ultimo_status integer,
    ultimo_erro   text,
    criado_em     timestamptz not null default now()
);

-- uma mensagem entra uma vez por rota. `provider_sid` vazio nunca conflita: é o
-- caso raro de mensagem sem id, e perder uma dessas por dedup seria pior.
create unique index if not exists uq_wa_qr_entrada_fila_sid
    on public.wa_qr_entrada_fila (conta_id, rota, provider_sid)
    where provider_sid <> '';

-- o que o drenador lê: pendente, na hora, em ordem de chegada
create index if not exists idx_wa_qr_entrada_fila_pendente
    on public.wa_qr_entrada_fila (conta_id, proxima_em, id)
    where entregue_em is null and parada_em is null;

-- retenção: o worker apaga o que já foi entregue, de hora em hora
create index if not exists idx_wa_qr_entrada_fila_entregue
    on public.wa_qr_entrada_fila (entregue_em)
    where entregue_em is not null;
