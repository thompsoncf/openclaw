-- 353_obra_venda_documentos.sql
-- A VENDA DA CASA e os PAPÉIS dela: o caminho do dinheiro. PR 3 de 4 do desenho
-- aprovado pelo dono em 25/09/2026 (docs/mockups/nicho_construcao.html, seções 06
-- e 10). Primeira conta: PX2 Empreendimentos (conta 33), que constrói casa popular
-- pra vender pelo Minha Casa Minha Vida.
--
-- POR QUE EXISTE. Na casa pronta vendida com financiamento da Caixa, o dinheiro
-- (financiamento + FGTS + subsídio) só cai pra construtora DEPOIS do registro do
-- contrato no cartório — e antes disso a casa precisa de habite-se, CND da obra
-- (aferição no SERO, Lei 8.212 art. 47) e averbação na matrícula. Da documentação
-- ao crédito, o mercado fala em 60 a 90 dias. Até aqui nada disso era guardado: a
-- construtora não via qual papel travava qual casa, nem quanto dinheiro estava
-- parado em casa pronta.
--
-- DUAS TABELAS:
--
-- * `obra_documentos`: um papel por linha (alvará, ART/RRT, CNO, habite-se, CND
--   da obra, averbação, matrícula, certidões da empresa), com situação, número,
--   data de emissão e validade. A lista de tipos é do CÓDIGO
--   (finance/obra_venda.DOCUMENTOS); o banco só guarda o que foi preenchido.
--
-- * `obra_vendas`: a venda da casa, uma por obra — comprador, faixa do MCMV,
--   modalidade, preço, avaliação da Caixa, financiamento, subsídio, FGTS,
--   entrada, e em que passo a venda está, com a data de cada passo. A MODALIDADE
--   é campo, e não suposição: o desenho parte da casa pronta financiada (o que a
--   PX2 faz, a confirmar com ela), mas à vista também existe.
--
-- Os títulos a receber da venda (a entrada, do comprador; o repasse, da Caixa)
-- são títulos COMUNS da tabela `titulos`, ligados ao centro de custo da obra. A
-- venda guarda os ids deles pra não criar duas vezes.
--
-- Nada é apagado: venda desfeita vira situação 'desistiu' (regra 0 do CLAUDE.md).
--
-- Aditiva e idempotente.

create table if not exists public.obra_documentos (
    id            bigserial primary key,
    conta_id      bigint not null references public.contas(id),
    obra_id       bigint not null references public.obras(id),
    tipo          text   not null,
    status        text   not null default 'pendente'
                  check (status in ('pendente', 'ok', 'nao_se_aplica')),
    numero        text   not null default '',
    emitido_em    date,
    vence_em      date,
    obs           text   not null default '',
    atualizado_em timestamptz not null default now(),
    unique (obra_id, tipo)
);

create table if not exists public.obra_vendas (
    obra_id                   bigint primary key references public.obras(id),
    conta_id                  bigint not null references public.contas(id),
    comprador                 text   not null default '',
    telefone                  text   not null default '',
    cliente_id                bigint,
    faixa                     smallint check (faixa is null or faixa between 1 and 4),
    modalidade                text   not null default 'financiada'
                              check (modalidade in ('financiada', 'avista', 'outro')),
    valor_venda_centavos      bigint check (valor_venda_centavos is null or valor_venda_centavos >= 0),
    valor_avaliacao_centavos  bigint check (valor_avaliacao_centavos is null or valor_avaliacao_centavos >= 0),
    financiamento_centavos    bigint check (financiamento_centavos is null or financiamento_centavos >= 0),
    subsidio_centavos         bigint check (subsidio_centavos is null or subsidio_centavos >= 0),
    fgts_centavos             bigint check (fgts_centavos is null or fgts_centavos >= 0),
    entrada_centavos          bigint check (entrada_centavos is null or entrada_centavos >= 0),
    situacao                  text   not null default 'documentacao'
                              check (situacao in ('documentacao', 'analise', 'aprovado',
                                                  'avaliacao', 'assinatura', 'registro',
                                                  'creditado', 'desistiu')),
    aprovado_em               date,
    avaliacao_em              date,
    assinatura_em             date,
    registro_em               date,
    creditado_em              date,
    titulo_entrada_id         bigint,
    titulo_repasse_id         bigint,
    obs                       text   not null default '',
    criado_em                 timestamptz not null default now(),
    atualizado_em             timestamptz not null default now()
);
create index if not exists idx_obra_vendas_conta on public.obra_vendas (conta_id, situacao);

-- rollback (manual, e só se nenhuma conta tiver venda ou documento):
--   drop table if exists public.obra_vendas;
--   drop table if exists public.obra_documentos;
