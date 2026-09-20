-- 301_respostas_rapidas.sql
-- AS RESPOSTAS RÁPIDAS do vendedor: o que ele digita o dia inteiro, a um toque.
--
-- POR QUÊ. O vendedor manda as mesmas frases dezenas de vezes por dia — valores,
-- endereço, o que está incluso, horário de visita. Hoje ele copia e cola do
-- WhatsApp do celular, que é exatamente o hábito que o app existe pra substituir:
-- o que sai por fora não entra no funil e chega sem nome.
--
-- DUAS DONAS, e é de propósito:
--   membro_id NULL  → é DA CONTA: o dono escreve e toda a equipe vê (o preço, o
--                     endereço, as condições — o que a empresa diz ao cliente).
--   membro_id = X   → é DAQUELE VENDEDOR: os jeitos dele, que só ele vê.
-- Sem as da conta, cada um manda uma versão diferente do preço; sem as pessoais,
-- a lista nasce vazia e ninguém preenche.
--
-- `usos` existe pra ordenar: quem manda o preço vinte vezes por dia não pode ter
-- que rolar a lista até achar. É contador de uso, não de pessoa — não diz QUEM
-- usou nem QUANDO, só quantas vezes.
--
-- Idempotente: só cria o que falta, e não toca em nenhuma tabela existente.
create table if not exists public.respostas_rapidas (
    id          bigserial primary key,
    conta_id    bigint      not null references contas(id) on delete restrict,
    -- NULL = da conta inteira; preenchido = só daquele vendedor
    membro_id   bigint,
    titulo      text        not null default '',
    texto       text        not null,
    usos        integer     not null default 0,
    criado_por  bigint,
    criado_em   timestamptz not null default now()
);

-- a consulta da tela é sempre "as desta conta que são da equipe OU minhas,
-- as mais usadas primeiro"
create index if not exists idx_resp_rapidas_conta
    on public.respostas_rapidas (conta_id, membro_id, usos desc, id desc);
