-- 017: escala do banco de precos (agregacao + historico)
--
-- (A) precos_vigentes: tabela agregada (1 linha por produto+loja+regiao+unidade)
--     Mediana dos preços dos últimos 90 dias. O comparador lê daqui (rápido).
--
-- (B) precos_observados_historico: cópia de preços antigos (> 6 meses)
--     precos_observados fica leve (~90 dias). Histórico fica guardado.

-- Tabela de precos vigentes (agregada, mediana)
create table if not exists precos_vigentes (
    id                      bigserial primary key,
    descricao_norm          text not null,           -- produto normalizado (chave)
    descricao_exemplo       text,                    -- um nome do produto pra exibir
    loja_id                 bigint references lojas(id) on delete set null,  -- (chave)
    mercado                 text,                    -- nome do estabelecimento
    regiao                  text,                    -- cidade/UF (chave)
    unidade                 text not null default 'UN',  -- (chave)
    valor_mediana_centavos  int not null,            -- mediana (robusto a outlier)
    n_observacoes           int not null default 1,  -- quantas observacoes entraram
    data_mais_recente       date,                    -- quando foi visto ultima vez
    atualizado_em           timestamptz not null default now(),

    unique (descricao_norm, loja_id, regiao, unidade)
);
create index if not exists idx_vigentes_busca on precos_vigentes(descricao_norm);
create index if not exists idx_vigentes_loja on precos_vigentes(loja_id, descricao_norm);

-- Tabela de historico (copia exata de precos_observados, sem limit de tempo)
create table if not exists precos_observados_historico (
    id                      bigserial primary key,
    descricao_norm          text not null,
    descricao_original      text not null,
    valor_unitario_centavos int not null,
    mercado                 text,
    regiao                  text,
    gtin                    text,
    data_compra             date not null,
    conta_id                bigint references contas(id) on delete set null,
    item_id                 bigint,
    fonte                   text not null default 'cupom',
    loja_id                 bigint references lojas(id) on delete set null,
    unidade                 text not null default 'UN',
    criado_em               timestamptz not null default now(),

    unique (id)  -- mesmos IDs de precos_observados (copia identica)
);
create index if not exists idx_historico_produto on precos_observados_historico(descricao_norm);
create index if not exists idx_historico_data on precos_observados_historico(data_compra);
