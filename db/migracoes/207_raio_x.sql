-- 207_raio_x.sql
-- O Raio-X (mockup docs/mockups/raio_x_como_fica.html, aprovado em 05/09/2026):
-- a aba do app do vendedor não precisa de tabela nenhuma — lê o que já existe.
-- O que precisa de tabela é a MENSAGEM DE SEGUNDA no grupo dos vendedores:
--
--   raio_x_config   por conta: qual grupo do WhatsApp recebe, e se está ligado.
--                   O dono escolhe em Equipe → "Raio-X de segunda". Sem linha, ou
--                   com ativo=false, a conta não recebe nada — ligado é escolha.
--
--   raio_x_envios   uma linha por (conta, segunda). É a TRAVA contra envio em
--                   dobro: o web roda com 2 workers e o ticker de 2 min roda nos
--                   dois; quem consegue inserir a linha manda, o outro vê o
--                   conflito e desiste. `enviado_em` nulo com `tentativas` < 5 é
--                   "tentou e não conseguiu": o ciclo seguinte tenta de novo, com
--                   10 min entre tentativas, e a mensagem que falhou fica em
--                   `erro` pra quem for olhar.
--
-- Aditiva e idempotente. Não encosta em chip, sessão ou canal: o envio sai pelo
-- caminho normal de `whatsapp_out.enviar`, só que pra um jid de grupo.

create table if not exists public.raio_x_config (
    conta_id      bigint primary key references public.contas(id) on delete cascade,
    grupo_jid     text,
    grupo_nome    text,
    ativo         boolean not null default true,
    atualizado_em timestamptz not null default now()
);

create table if not exists public.raio_x_envios (
    conta_id      bigint not null references public.contas(id) on delete cascade,
    -- a segunda-feira da semana ENVIADA (a mensagem fala da semana anterior)
    semana        date   not null,
    texto         text,
    enviado_em    timestamptz,
    erro          text,
    tentativas    int    not null default 1,
    atualizado_em timestamptz not null default now(),
    primary key (conta_id, semana)
);

-- rollback:
--   drop table if exists public.raio_x_envios;
--   drop table if exists public.raio_x_config;
