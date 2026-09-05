-- 201_contrato_aditivos.sql
-- O termo aditivo: EMENDA ao contrato, não contrato novo.
--
-- POR QUE TABELA PRÓPRIA, E NÃO UMA LINHA EM `contratos`
--
-- A 164 reservou `contratos.substitui_id` e chamou o aditivo de "contrato novo que
-- substitui um anterior". O modelo em papel do dono (16 páginas, lidas em
-- 04/09/2026: 1 modelo em branco e 4 aditivos reais já assinados) diz o contrário,
-- duas vezes no mesmo documento:
--
--   "…cujas cláusulas permanecem em vigor, EXCETO naquilo que forem alteradas
--    pelo presente instrumento."
--   "Permanecem INALTERADAS todas as demais cláusulas e condições estabelecidas
--    no contrato original."
--
-- O aditivo real tem UMA página e cita só o que mudou. O contrato original
-- continua valendo e continua sendo *o* contrato. Isso é emenda, não substituição.
--
-- E encaixar emenda em `contratos` quebraria duas coisas que hoje funcionam:
--
--  1. `ux_contratos_orcamento` é unique em `(orcamento_id) where substitui_id is
--     null`. Um aditivo com o mesmo orçamento e sem `substitui_id` estoura o
--     índice.
--  2. Com `substitui_id` preenchido pra caber no índice, ele some de
--     `por_orcamento()` — que filtra exatamente `substitui_id is null`. E aqui
--     mora uma armadilha que vale registrar: aquela função se descreve como "o
--     contrato VIVO (o que não foi substituído por aditivo)", mas o predicado quer
--     dizer "o que não SUBSTITUI outro", isto é, o ORIGINAL. Os dois sentidos são
--     opostos, e hoje ninguém percebe porque não existe aditivo nenhum.
--
-- O custo desta tabela é repetir ~8 colunas de assinatura. O que se compra é que
-- NENHUMA consulta existente muda de sentido: as três travas de edição
-- (`painel_servicos`, `cockpit`, `vendas`) e o relatório de contratos continuam
-- lendo `contratos` como sempre leram.
--
-- O QUE O ADITIVO ALTERA
-- Cinco coisas, que é a lista que o dono deu como habitual: data, horário,
-- quantidade de convidados, serviços contratados, e — dependendo da alteração —
-- valor. As quatro primeiras vão em `alteracoes` (jsonb, uma entrada por bloco
-- marcado); dinheiro tem coluna própria, porque é o que vira título a receber e
-- consulta de dinheiro não se faz cavando json.
--
-- Aditivo e idempotente.
create table if not exists public.contrato_aditivos (
    id              bigserial primary key,
    conta_id        bigint not null references public.contas(id) on delete restrict,
    -- COM FK, ao contrário de `contratos.orcamento_id`. Lá o comentário da 164
    -- explica que documento assinado não pode ser impedido de existir pelo
    -- documento comercial que o originou. Aqui é o oposto: um aditivo sem o
    -- contrato que ele emenda não é documento nenhum — o texto inteiro dele é
    -- "o contrato nº N fica alterado em tal ponto".
    contrato_id     bigint not null references public.contratos(id),
    -- 1º aditivo, 2º aditivo… daquele contrato. É como o papel se refere a si
    -- mesmo, e é por contrato, não por conta: "2º termo aditivo ao contrato nº 5".
    ordem           int    not null,
    status          text   not null default 'enviado'
                    check (status in ('enviado','assinado','cancelado')),
    -- o de→para de cada bloco marcado. Lista de objetos, um por alteração:
    --   {"campo":"data","de":"2027-01-15","para":"2027-01-22"}
    --   {"campo":"horario","de":{...},"para":{...}}
    --   {"campo":"convidados","de":115,"para":140}
    --   {"campo":"servicos","saem":[...],"entram":"texto livre"}
    -- Guardar o DE junto com o PARA é o que permite ao documento dizer "em
    -- substituição à quantidade originalmente estabelecida de 115" um ano depois,
    -- quando o orçamento já foi alterado pelo próprio aditivo.
    alteracoes      jsonb  not null default '[]'::jsonb,
    -- CONGELADO na assinatura, mesma regra do contrato: grava-se o que o cliente
    -- LEU. Nulo enquanto ninguém assinou, e aí o documento é montado ao vivo.
    texto           jsonb,
    -- dinheiro. `antes` fica gravado pelo mesmo motivo do `de` das alterações:
    -- reler o orçamento depois não devolve o valor que valia na hora.
    valor_antes_centavos bigint,
    valor_novo_centavos  bigint,
    -- a diferença que o cliente paga por esta alteração, separada da taxa de
    -- reagendamento da cláusula 7.2 — no documento as duas aparecem discriminadas,
    -- e no título a receber a soma é uma coisa só.
    diferenca_centavos   bigint not null default 0,
    taxa_centavos        bigint not null default 0,
    vencimento      date,
    forma_pagamento text   not null default '',
    -- o título a receber gerado na assinatura. Sem FK de propósito: se alguém
    -- apagar o título, o aditivo assinado não pode deixar de existir.
    titulo_id       bigint,
    token           text,
    assinado_em     timestamptz,
    assinado_por    text,
    assinado_doc    text,
    assinado_ip     text,
    cancelado_em    timestamptz,
    criado_em       timestamptz not null default now(),
    criado_por      text   not null default ''
);

-- a série por contrato, garantida pelo banco
create unique index if not exists ux_aditivos_contrato_ordem
    on public.contrato_aditivos (contrato_id, ordem);

-- o link público, mesmo desenho do contrato e da proposta
create unique index if not exists ux_aditivos_token
    on public.contrato_aditivos (token) where token is not null;

-- UM aditivo em aberto por contrato. Dois links vivos pro mesmo contrato é o
-- cliente assinando o errado — e como o aditivo é o que muda data e valor, assinar
-- o errado é o evento acontecendo no dia errado. Quem quiser corrigir cancela o
-- aberto e faz outro; o código checa antes e explica, este índice é a rede.
create unique index if not exists ux_aditivos_um_aberto
    on public.contrato_aditivos (contrato_id) where status = 'enviado';

-- "o que está esperando assinatura", a pergunta da tela
create index if not exists idx_aditivos_conta_status
    on public.contrato_aditivos (conta_id, status, id desc);

-- rollback:
--   drop table if exists public.contrato_aditivos;
