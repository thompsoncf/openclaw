-- 173_cockpit_lembrete.sql
-- "Manter conectado" no Cockpit: sessão que NÃO expira sozinha.
--
-- O PROBLEMA. O vendedor entra por link mágico (15 min, cockpit_acesso) e o que o
-- segura depois é o cookie de sessão do portal, com 7 dias (web/app.py). Só que ele
-- caía antes disso, e a tela onde ele aterrissava — /cockpit/login — só oferecia
-- "te mando um link por e-mail". Sem senha ali, ficar de fora dependia de e-mail
-- chegar e de abrir em 15 minutos, no meio do expediente.
--
-- E a queda nem sempre é o prazo vencendo: quem abre o link mágico DENTRO do app de
-- e-mail (Gmail/Outlook abrem num navegador interno próprio) ganha o cookie NAQUELE
-- contexto. Ao tocar no ícone do Zaq na tela inicial, é outro contexto, sem cookie —
-- e a tela de entrada volta, com a sessão "válida" em outro lugar.
--
-- POR QUE UMA TABELA, e não simplesmente aumentar o max_age do cookie:
--
--  1. o max_age do SessionMiddleware é GLOBAL — mexer nele estenderia junto a
--     sessão do dono no painel, que é outra decisão e outro risco;
--  2. "indeterminado" sem poder REVOGAR seria irresponsável: celular perdido, ou
--     vendedor que sai da empresa, precisam ter o acesso cortado de algum lugar.
--     Aqui basta marcar `revogado_em` — desativar o membro também corta, porque a
--     validação relê `membros.ativo` a cada uso;
--  3. dá pra ver e encerrar aparelho por aparelho depois, sem nova migração.
--
-- GUARDA SÓ O HASH DO TOKEN, nunca o token. Quem lê esta tabela não consegue se
-- passar por ninguém: o valor que vale está só no cookie do aparelho. Mesmo motivo
-- de `membros.senha_hash` não guardar a senha.
--
-- Aditivo e idempotente.

create table if not exists public.cockpit_lembrete (
    id          bigserial primary key,
    conta_id    bigint not null references public.contas(id)  on delete cascade,
    membro_id   bigint not null references public.membros(id) on delete cascade,
    -- sha256 do token que está no cookie. Único: dois aparelhos nunca colidem, e
    -- a busca da validação é por igualdade neste campo.
    token_hash  text   not null unique,
    -- de onde veio, pra a pessoa reconhecer o aparelho numa lista futura. Curto de
    -- propósito: não é telemetria, é só "iPhone" vs "Chrome no PC".
    aparelho    text   not null default '',
    criado_em   timestamptz not null default now(),
    ultimo_uso  timestamptz,
    -- SEM expira_em: é este o pedido. O acesso vale até alguém revogar, o membro
    -- ser desativado, ou o vendedor sair pelo botão Sair.
    revogado_em timestamptz
);

-- a pergunta da validação, feita a cada request que chega sem sessão: "este token
-- vale?". Parcial em `revogado_em is null` porque token revogado nunca mais é
-- consultado — e o índice não precisa carregar o histórico.
create unique index if not exists ux_cockpit_lembrete_token
    on public.cockpit_lembrete (token_hash) where revogado_em is null;

-- "quais aparelhos deste membro estão conectados" — a tela de encerrar sessão, e o
-- corte em massa quando o membro é desativado.
create index if not exists idx_cockpit_lembrete_membro
    on public.cockpit_lembrete (membro_id, revogado_em);

-- rollback:
--   drop table if exists public.cockpit_lembrete;
