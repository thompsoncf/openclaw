-- 053_modulo_pj.sql
-- Módulo Empresa (PJ) — Fase 1. Só tabelas NOVAS (zero alter nas existentes):
-- rodar esta migração não muda nada pra quem está no ar.

-- ── Contas a pagar e a receber ──────────────────────────────────────────────
create table if not exists titulos (
    id               bigserial primary key,
    conta_id         bigint not null references contas(id) on delete restrict,
    tipo             text   not null check (tipo in ('pagar','receber')),
    descricao        text   not null,
    contraparte      text   not null default '',   -- fornecedor (pagar) / cliente (receber)
    valor_centavos   int    not null check (valor_centavos > 0),
    vencimento       date   not null,
    status           text   not null default 'aberto'
                     check (status in ('aberto','pago','cancelado')),
    recorrente       boolean not null default false, -- mensal: baixa gera o do mês seguinte
    categoria        text   not null default '',     -- categoria no livro-caixa na baixa
    lancamento_id    bigint references lancamentos(id) on delete set null, -- baixa amarrada ao caixa
    cobranca_link_url text,                          -- "cobrar via Pix" (Asaas) nos a receber
    pago_em          date,
    criado_por       bigint references membros(id) on delete set null,
    criado_em        timestamptz not null default now()
);
create index if not exists idx_titulos_conta
    on titulos (conta_id, status, vencimento);

-- ── Equipe (controle GERENCIAL — a folha oficial/eSocial segue com o contador) ─
create table if not exists funcionarios (
    id               bigserial primary key,
    conta_id         bigint not null references contas(id) on delete restrict,
    nome             text   not null,
    cargo            text   not null default '',
    salario_centavos int    not null default 0 check (salario_centavos >= 0),
    dia_pagamento    int    not null default 5 check (dia_pagamento between 1 and 28),
    pro_labore       boolean not null default false, -- retirada de sócio (sem encargos no custo)
    ativo            boolean not null default true,
    admitido_em      date,
    criado_em        timestamptz not null default now()
);
create index if not exists idx_func_conta on funcionarios (conta_id, ativo);

-- ── Eventos de folha por competência (vale, extra, desconto, pagamento) ──────
create table if not exists folha_eventos (
    id               bigserial primary key,
    conta_id         bigint not null references contas(id) on delete restrict,
    funcionario_id   bigint not null references funcionarios(id) on delete cascade,
    tipo             text   not null check (tipo in ('vale','extra','desconto','pagamento')),
    valor_centavos   int    not null check (valor_centavos > 0),
    competencia      date   not null,               -- 1º dia do mês de referência
    descricao        text   not null default '',
    lancamento_id    bigint references lancamentos(id) on delete set null,
    criado_em        timestamptz not null default now()
);
create index if not exists idx_folha_conta on folha_eventos (conta_id, competencia);
create index if not exists idx_folha_func  on folha_eventos (funcionario_id, competencia);

-- ── Módulo no catálogo (incluso no plano PJ; conta_modulos = cortesia/override) ─
insert into modulos (codigo, nome, preco_centavos)
values ('pj', 'Módulo Empresa (PJ)', 0)
on conflict (codigo) do nothing;
