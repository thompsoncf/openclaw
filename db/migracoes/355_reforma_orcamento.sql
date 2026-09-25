-- 355_reforma_orcamento.sql
-- O ORÇAMENTO DA REFORMA, com aceite do cliente e cobrança por etapa. PR 4 de 4 do
-- desenho aprovado pelo dono em 25/09/2026 (docs/mockups/nicho_construcao.html,
-- seção 09). Primeira conta: PX2 Empreendimentos (conta 33), que também faz
-- reforma pra cliente.
--
-- POR QUE UMA TABELA DA OBRA, e não um terceiro modo em `orcamentos`. O orçamento
-- de hoje tem dois modos (evento e recorrente), e cada um puxa proposta, contrato,
-- funil e títulos que TODAS as contas usam. Um terceiro modo mexeria nisso tudo
-- pra atender um ramo. A reforma já é uma OBRA (migração 351), com etapas e centro
-- de custo; o orçamento dela mora ao lado, e o que ele precisa do resto do sistema
-- — o título a receber — é o título comum.
--
-- O QUE A LEI JÁ MANDA TER, virando coluna:
--   * CDC, art. 40: o orçamento prévio discrimina mão de obra, material e
--     equipamento (o `tipo` de cada item em `itens`), traz forma de pagamento e
--     datas, e vale 10 dias (`validade_ate`).
--   * CC, art. 610: material só está incluso se estiver escrito
--     (`material_incluso`).
--   * CC, art. 619: serviço fora do combinado só com aceite por escrito — por
--     isso o ADITIVO é uma VERSÃO nova (`versao` 2, 3...), com link e aceite
--     próprios, e não uma linha a mais no orçamento já aceito.
--
-- O ACEITE é registrado como o da proposta de hoje (web/proposta.py): nome,
-- documento, IP e data. Aceito, cada parcela vira um título a receber no centro
-- da obra. As parcelas ligadas a uma ETAPA ficam "liberadas pra cobrar" quando a
-- etapa é concluída.
--
-- Nada é apagado (regra 0): orçamento recusado continua lá, com o status.
--
-- Aditiva e idempotente.

create table if not exists public.obra_orcamentos (
    id                bigserial primary key,
    conta_id          bigint   not null references public.contas(id),
    obra_id           bigint   not null references public.obras(id),
    -- 1 é o orçamento; 2 em diante, os aditivos
    versao            smallint not null default 1 check (versao >= 1),
    -- [{servico, tipo: mao_de_obra|material|equipamento, unidade, quantidade,
    --   valor_unit_centavos}]
    itens             jsonb    not null default '[]'::jsonb,
    material_incluso  boolean  not null default true,
    prazo_dias        smallint check (prazo_dias is null or prazo_dias > 0),
    validade_ate      date,
    garantia          text     not null default '',
    escopo            text     not null default '',
    modelo_pagamento  text     not null default 'etapas'
                      check (modelo_pagamento in ('etapas', 'rcb', 'avista')),
    -- [{rotulo, pct, valor_centavos, etapa (chave da obra_etapas | null)}]
    parcelas          jsonb    not null default '[]'::jsonb,
    total_centavos    bigint   not null default 0 check (total_centavos >= 0),
    status            text     not null default 'rascunho'
                      check (status in ('rascunho', 'enviado', 'aceito', 'recusado')),
    token             text     unique,
    aceito_em         timestamptz,
    aceito_nome       text,
    aceito_doc        text,
    aceito_ip         text,
    -- os títulos a receber criados no aceite, na ordem das parcelas
    titulos           jsonb    not null default '[]'::jsonb,
    criado_em         timestamptz not null default now(),
    atualizado_em     timestamptz not null default now(),
    unique (obra_id, versao)
);
create index if not exists idx_obra_orcamentos_conta on public.obra_orcamentos (conta_id, status);

-- rollback (manual, e só se nenhuma conta tiver orçamento de reforma):
--   drop table if exists public.obra_orcamentos;
