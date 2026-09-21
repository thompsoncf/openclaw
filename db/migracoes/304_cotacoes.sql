-- 304_cotacoes.sql
-- A COTAÇÃO: o que acontece ANTES da apólice existir.
--
-- O PEDIDO (dono, 21/09/2026): "quero implementar uma API pra Liberal Seguros pra
-- cotação de seguros e, caso tenha, o envio de dados pra apólice por seguradora."
--
-- POR QUE ESTA TABELA EXISTE. A carteira (migração 278) começa na PROPOSTA — o
-- papel que a seguradora já emitiu. Tudo que vem antes dela (o risco que o cliente
-- passou, os preços que voltaram de cada seguradora, a oferta que ele escolheu)
-- não era guardado em lugar nenhum: vivia no multicálculo de terceiro, no
-- WhatsApp e na cabeça do corretor. O efeito prático é que a corretora não
-- consegue responder as duas perguntas que mais valem dinheiro pra ela: "quantas
-- cotações viram apólice?" e "quando eu perco, perco pra qual preço?".
--
-- A DECISÃO DE DESENHO, e é a que amarra o resto: a tabela é AGNÓSTICA DE
-- PROVEDOR (decisão do dono, 21/09/2026). `risco` é jsonb, `provedor` é texto, e
-- nenhuma coluna aqui conhece InsureMO, Segfy, Quiver, TEx ou seguradora nenhuma.
-- O motivo é concreto: no Brasil quem cota auto em 18–20 seguradoras é o
-- multicálculo, o contrato ainda não existe, e uma tabela modelada no formato do
-- primeiro provedor teria que ser migrada no dia em que ele trocasse. O conector
-- é um arquivo em `finance/cotacao_provedores.py`; o banco não muda com ele.
--
-- O PROVEDOR 'manual' É O PADRÃO, E NÃO É PLACEHOLDER. Enquanto não houver API
-- contratada, o corretor digita as ofertas que ele mesmo levantou e o comparativo
-- funciona igual — só sem o passo automático. É isso que faz a tela nascer útil no
-- dia 1 em vez de esperar contrato de terceiro.
--
-- SOBRE O PRÊMIO, E ESTE É O PONTO EM QUE JÁ ERRAMOS UMA VEZ. A migração 278
-- guarda em `apolices.premio_centavos` SEMPRE o LÍQUIDO (sem IOF), porque a
-- comissão do corretor incide sobre o líquido — o mockup das apólices somou o IOF
-- e mostrou R$ 817,71 onde o certo era R$ 761,51. Aqui a oferta repete a mesma
-- regra, com um cuidado a mais: quando o provedor manda só o TOTAL e não separa o
-- IOF, `premio_liquido_centavos` fica NULO de propósito. Nulo é a resposta
-- honesta; estimar a separação seria inventar comissão — o mesmo erro, de novo,
-- agora automatizado.
--
-- Aditivo e idempotente.

create table if not exists public.cotacoes (
    id           bigserial primary key,
    conta_id     bigint not null references public.contas(id) on delete restrict,
    -- o cliente é OPCIONAL pelo mesmo motivo da 278: cota-se pra quem ainda não
    -- está na carteira mais vezes do que pra quem está, e exigir o cadastro antes
    -- seria o sistema escolhendo a ordem do trabalho da corretora.
    cliente_id   bigint references public.clientes(id),
    corretor_id  bigint references public.membros(id),
    ramo         text not null default 'auto',
    -- de onde ela veio. Os três caminhos que o dono pediu na mesma conversa: a
    -- tela do corretor, a API que o site da corretora chama, e o WhatsApp.
    origem       text not null default 'painel'
                 check (origem in ('painel','api','whatsapp')),
    provedor     text not null default 'manual',
    situacao     text not null default 'rascunho'
                 check (situacao in ('rascunho','cotada','falhou','escolhida','proposta','perdida')),
    -- O RISCO NORMALIZADO (o que o provedor precisa saber pra dar preço). Jsonb
    -- pelo mesmo motivo do `bem` na 278: auto pergunta placa, chassi e FIPE;
    -- residencial pergunta metragem; vida pergunta idade. Uma coluna por campo de
    -- cada ramo seria uma tabela de sessenta colunas nulas em toda linha.
    risco        jsonb not null default '{}'::jsonb,
    -- por que falhou, em português, pra tela poder dizer. Guardado porque falha de
    -- provedor que só vai pro log é falha que ninguém conserta.
    erro         text,
    -- por que o cliente NÃO fechou. Coluna própria e não o `erro` acima: são duas
    -- perguntas diferentes ("o provedor caiu" × "perdi pro preço da concorrente"),
    -- e juntar as duas numa coluna só é perder as duas respostas. Mesmo desenho do
    -- `perda_motivo` das apólices (migração 287).
    perda_motivo text,
    -- a apólice que nasceu desta cotação. É o elo que responde "quantas cotações
    -- viram apólice" sem cruzar nome de cliente com data.
    apolice_id   bigint references public.apolices(id),
    criado_em    timestamptz not null default now(),
    atualizado_em timestamptz not null default now(),
    cotado_em    timestamptz
);

create index if not exists ix_cotacoes_conta
    on public.cotacoes (conta_id, criado_em desc);

create index if not exists ix_cotacoes_corretor
    on public.cotacoes (conta_id, corretor_id) where corretor_id is not null;

create index if not exists ix_cotacoes_cliente
    on public.cotacoes (conta_id, cliente_id) where cliente_id is not null;


-- As ofertas que voltaram — uma linha por seguradora. É a tabela que transforma
-- "cotei" em comparativo, e é dela que sai o número que o Raio-X vai querer um
-- dia: o preço pelo qual a corretora perdeu.
create table if not exists public.cotacao_ofertas (
    id            bigserial primary key,
    cotacao_id    bigint not null references public.cotacoes(id) on delete cascade,
    seguradora    text   not null,
    produto       text   not null default '',
    -- o LÍQUIDO, como em `apolices.premio_centavos`. NULO quando o provedor não
    -- separou o IOF — ver o cabeçalho.
    premio_liquido_centavos bigint,
    iof_centavos            bigint,
    -- o que o cliente paga. Este SEMPRE existe: é o número que aparece na tela e
    -- o único que toda seguradora manda.
    premio_total_centavos   bigint not null default 0,
    franquia_centavos       bigint,
    comissao_pct  numeric(5,2),
    parcelas      int,
    coberturas    jsonb  not null default '[]'::jsonb,
    validade      date,
    -- o id da oferta no provedor. É ele que o envio pra emissão manda de volta —
    -- sem guardar, a oferta escolhida na tela não tem como ser encontrada lá.
    ref_externa   text,
    -- a resposta crua do provedor, inteira. Custa pouco e é a única coisa que
    -- permite entender uma divergência de preço depois que ela virou discussão
    -- com a seguradora.
    bruto         jsonb  not null default '{}'::jsonb,
    escolhida     boolean not null default false,
    criado_em     timestamptz not null default now()
);

create index if not exists ix_cotacao_ofertas
    on public.cotacao_ofertas (cotacao_id, premio_total_centavos);

-- Uma cotação tem UMA oferta escolhida. Duas seria a corretora tendo fechado o
-- mesmo seguro duas vezes — e a proposta nasceria do preço errado.
create unique index if not exists ux_cotacao_escolhida
    on public.cotacao_ofertas (cotacao_id) where escolhida;


-- A CHAVE DA API que a corretora entrega pro site/parceiro dela. O token não é
-- guardado: guarda-se o sha256 dele e os 8 primeiros caracteres, que servem só
-- pra tela dizer qual chave é qual. Vazado o banco, ninguém cota no nome dela —
-- e perdida a chave, não há como recuperar, só emitir outra. É de propósito.
create table if not exists public.cotacao_chaves (
    id            bigserial primary key,
    conta_id      bigint not null references public.contas(id) on delete cascade,
    rotulo        text   not null default '',
    prefixo       text   not null,
    token_hash    text   not null,
    ativa         boolean not null default true,
    criado_em     timestamptz not null default now(),
    criado_por    bigint,
    ultimo_uso_em timestamptz
);

create unique index if not exists ux_cotacao_chaves_hash
    on public.cotacao_chaves (token_hash);

create index if not exists ix_cotacao_chaves_conta
    on public.cotacao_chaves (conta_id) where ativa;

-- rollback:
--   drop table if exists public.cotacao_chaves;
--   drop table if exists public.cotacao_ofertas;
--   drop table if exists public.cotacoes;
