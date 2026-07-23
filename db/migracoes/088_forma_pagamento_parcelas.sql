-- 088_forma_pagamento_parcelas.sql
-- Duas coisas ligadas ao "como foi pago":
--
-- 1) forma_pagamento: etiqueta CANONICA por lancamento (pix|debito|credito|
--    especie|boleto|transferencia|outro). Antes so' existia `pagamento` (texto
--    livre, quase nunca preenchido). A coluna nova nao substitui: `pagamento`
--    segue livre pra observacao (bandeira do cartao, "pix do fulano" etc) e
--    `forma_pagamento` e' o dado estruturado que os relatorios/previsao usam.
--    Nullable/default '' -> zero impacto no que ja' esta' no ar.
--
-- 2) parcelas_cartao: previsao da FATURA do cartao de credito. Quando o gasto e'
--    parcelado (ex: 1200 em 12x), a parcela do mes vira despesa normal e as
--    futuras ficam AQUI como 'previsto' - NAO entram no saldo ate' vencerem.
--    Quando a competencia chega, a parcela e' promovida a lancamento (status
--    'lancado', lancamento_id preenchido). grupo amarra as N parcelas da mesma
--    compra. Isso espelha o padrao titulos -> fluxo_projetado que ja' existe no
--    modulo PJ, agora pro cartao do PF tambem.

alter table lancamentos
    add column if not exists forma_pagamento text not null default '';

create table if not exists parcelas_cartao (
    id             bigserial primary key,
    conta_id       bigint not null references contas(id) on delete restrict,
    membro_id      bigint references membros(id) on delete set null,
    grupo          uuid   not null,          -- amarra as N parcelas da compra
    descricao      text   not null default '',
    categoria      text   not null,
    valor_centavos bigint not null check (valor_centavos >= 0),  -- valor da parcela
    parcela_num    int    not null check (parcela_num >= 1),     -- 1..total
    parcela_total  int    not null check (parcela_total >= 1),
    competencia    date   not null,          -- 1o dia do mes em que a parcela cai
    status         text   not null default 'previsto'
                       check (status in ('previsto', 'lancado', 'cancelado')),
    lancamento_id  bigint references lancamentos(id) on delete set null,
    criado_em      timestamptz not null default now()
);

create index if not exists idx_parcelas_conta
    on parcelas_cartao (conta_id);
-- consulta quente: parcelas previstas ja' vencidas (materializacao) e previsao
create index if not exists idx_parcelas_previsao
    on parcelas_cartao (conta_id, status, competencia);
