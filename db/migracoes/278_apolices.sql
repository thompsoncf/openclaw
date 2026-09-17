-- 278_apolices.sql
-- A carteira de apólices da corretora, e o relógio da renovação.
--
-- O PEDIDO (dono, 17/09/2026), depois de mandar uma proposta da Allianz:
-- "baseado nessa apólice tem dados suficiente pra montar o banco de clientes com
-- informações importantes para gerar o alerta". Mockup aprovado em
-- docs/mockups/apolices_alerta_renovacao.html; as quatro decisões dele, verbatim:
-- "60/30/15, percentual por seguradora, alerta pro corretor, começa por auto".
--
-- POR QUE ESTA TABELA EXISTE. Quando o perfil de Raio-X da corretora entrou
-- (migração 242, `finance/raio_x_perfil.py`), `fu_festa_dias` ficou NULO com este
-- comentário: "ela TEM segundo relógio, e é o melhor de todos — o fim da vigência.
-- Mas o dado não é guardado em lugar nenhum ainda." Esta tabela é o dado. Quem
-- vende festa tem a data da festa; quem vende seguro tem o fim da vigência — e é
-- a mesma mecânica de contagem regressiva, com outro nome.
--
-- POR QUE `bem`, `condutor` E `coberturas` SÃO JSONB. Auto tem placa, chassi e
-- FIPE; residencial tem endereço e metragem; vida tem beneficiário. Uma coluna por
-- campo de cada ramo viraria uma tabela com sessenta colunas nulas em toda linha.
-- O que é IGUAL em todo ramo — vigência, prêmio, comissão, situação — é coluna de
-- verdade, porque é por onde se consulta e é o que o alerta lê.
--
-- SITUACAO TEM CINCO ESTADOS PORQUE O PAPEL TEM DOIS. O documento que o dono
-- mandou é uma PROPOSTA (nº 139041981), não uma apólice: a seguradora tem 25 dias
-- pra aceitar, há cobertura provisória nesse meio-tempo, e o número da apólice
-- ainda nem existe. Modelar só "apólice" perderia o começo do ciclo — que é
-- justamente onde o corretor precisa de ajuda.
--
-- Aditivo e idempotente.

create table if not exists public.apolices (
    id                bigserial primary key,
    conta_id          bigint not null references public.contas(id) on delete restrict,
    -- o segurado. Fica opcional de propósito: a corretora digita a apólice antes de
    -- o cliente existir na carteira mais vezes do que o contrário, e recusar a
    -- apólice por causa disso seria o sistema escolhendo a ordem do trabalho dela.
    cliente_id        bigint references public.clientes(id),
    -- de quem é o alerta (decisão do dono: "alerta pro corretor"). Nulo = é do dono.
    corretor_id       bigint references public.membros(id),
    seguradora        text   not null,
    ramo              text   not null default 'auto',
    numero_proposta   text,
    numero_apolice    text,
    vigencia_inicio   date,
    -- O CAMPO DE OURO: sozinho ele gera o alerta inteiro. NOT NULL porque apólice
    -- sem fim de vigência não entra em régua nenhuma — seria uma linha que parece
    -- coberta e nunca avisa.
    vigencia_fim      date   not null,
    situacao          text   not null default 'proposta'
                      check (situacao in ('proposta','vigente','renovada','vencida','cancelada')),
    premio_centavos   bigint not null default 0,
    iof_centavos      bigint not null default 0,
    franquia_centavos bigint not null default 0,
    -- comissão: o percentual sai do padrão da seguradora (tabela abaixo) e pode ser
    -- sobrescrito aqui. `comissao_centavos` é o valor fechado quando a corretora
    -- souber o número exato — aí ele manda, e o percentual vira histórico.
    comissao_pct      numeric(5,2),
    comissao_centavos bigint,
    classe_bonus      text,
    parcelas          int,
    dia_vencimento    int,
    bem               jsonb  not null default '{}'::jsonb,
    condutor          jsonb  not null default '{}'::jsonb,
    coberturas        jsonb  not null default '[]'::jsonb,
    -- aponta pra apólice anterior: é a carteira contando a própria história.
    renovacao_de      bigint references public.apolices(id),
    obs               text,
    criado_em         timestamptz not null default now(),
    atualizado_em     timestamptz not null default now()
);

-- A consulta da régua: "o que vence entre hoje e hoje+90, e ainda está de pé".
-- Parcial porque renovada/vencida/cancelada nunca entram em alerta, e são elas que
-- crescem sem parar — o índice fica do tamanho da carteira VIVA, não do histórico.
create index if not exists ix_apolices_renovacao
    on public.apolices (conta_id, vigencia_fim)
    where situacao in ('proposta','vigente');

create index if not exists ix_apolices_cliente
    on public.apolices (conta_id, cliente_id) where cliente_id is not null;

create index if not exists ix_apolices_corretor
    on public.apolices (conta_id, corretor_id) where corretor_id is not null;

-- Dois cadastros da mesma apólice é a carteira mentindo duas vezes: conta o dobro
-- de prêmio e dispara o alerta duplicado. Só vale quando o número existe — na fase
-- de PROPOSTA ele ainda não foi emitido, e nulo não colide com nulo no Postgres.
create unique index if not exists ux_apolices_numero
    on public.apolices (conta_id, lower(seguradora), numero_apolice)
    where numero_apolice is not null and numero_apolice <> '';


-- O percentual padrão por seguradora e ramo (decisão do dono: "percentual por
-- seguradora"). A comissão NÃO aparece em lugar nenhum do documento da apólice, e
-- faz sentido: é papel do cliente, e o cliente não vê quanto o corretor ganha. Mas
-- é o número em que o Raio-X da corretora se apoia ("comissão proposta × fechada"),
-- então ele tem que vir de algum lugar — vem daqui.
--
-- `ramo = ''` é o curinga: "Allianz, qualquer ramo, 20%". A busca tenta o ramo
-- exato primeiro e cai no curinga depois, então a corretora cadastra uma linha por
-- seguradora e só detalha onde o percentual foge do padrão.
create table if not exists public.seguros_comissao (
    id            bigserial primary key,
    conta_id      bigint not null references public.contas(id) on delete restrict,
    seguradora    text   not null,
    ramo          text   not null default '',
    pct           numeric(5,2) not null,
    criado_em     timestamptz not null default now(),
    atualizado_em timestamptz not null default now()
);

create unique index if not exists ux_seguros_comissao
    on public.seguros_comissao (conta_id, lower(seguradora), ramo);


-- O dedup do alerta de renovação. MESMO motivo das migrações 128 e 171: o CHECK da
-- 101 não conhece o valor novo, e `lembretes._primeira_vez` insere numa conexão
-- própria — a CheckViolation subiria até `_rodar()` e abortaria o tick INTEIRO,
-- derrubando o resumo do dia de todas as contas seguintes. O nome do constraint é
-- estável desde a 101.
alter table public.lembretes_enviados drop constraint if exists lembretes_enviados_tipo_check;
alter table public.lembretes_enviados add constraint lembretes_enviados_tipo_check
    check (tipo in ('resumo','aviso','aviso_convidado','aniversario','renovacao'));

-- rollback:
--   drop table if exists public.seguros_comissao;
--   drop table if exists public.apolices;
--   alter table public.lembretes_enviados drop constraint if exists lembretes_enviados_tipo_check;
--   alter table public.lembretes_enviados add constraint lembretes_enviados_tipo_check
--       check (tipo in ('resumo','aviso','aviso_convidado','aniversario'));
