-- 132_plano_contas_centros_custo.sql
-- Plano de Contas (GLOBAL, do sistema) + Centros de Custo (por conta) + os dois
-- campos opcionais no livro-caixa. Fase 1: só estrutura + seed. NÃO-destrutivo e
-- idempotente — rodar não muda nada pra quem já está no ar (colunas novas entram
-- NULL, categoria livre continua valendo).
--
--   • plano_contas          → árvore contábil ÚNICA, igual pra todas as contas
--                             (ninguém cria/edita; o sistema já vem preenchido).
--   • plano_conta_habilitada→ liga/desliga POR conta. Linha ausente = habilitada
--                             (default-on): só grava quando o cliente desliga algo.
--   • centros_custo         → cada conta cadastra os seus (unidade, filial, projeto).
--   • lancamentos.plano_conta_id / centro_custo_id → classificação opcional.

-- ── Plano de contas GLOBAL (sem conta_id: é o mesmo pra todo mundo) ──────────
-- grupo = linha da DRE (1..7); a árvore sintética (nomes dos grupos) vive no
-- código (finance/plano_contas.py). natureza = sinal na DRE.
create table if not exists plano_contas (
    id        bigserial primary key,
    codigo    text     not null unique,               -- ex: '5.1.03'
    nome      text     not null,                       -- ex: 'Marketing e Publicidade'
    grupo     smallint not null check (grupo between 1 and 7),
    natureza  text     not null check (natureza in ('receita','despesa')),
    ordem     smallint not null default 0
);

-- ── Liga/desliga do plano POR conta (ausência = habilitada, default-on) ──────
create table if not exists plano_conta_habilitada (
    conta_id       bigint  not null references contas(id) on delete cascade,
    plano_conta_id bigint  not null references plano_contas(id) on delete cascade,
    habilitada     boolean not null default true,
    primary key (conta_id, plano_conta_id)
);

-- ── Centros de custo POR conta (o cliente cadastra os dele) ──────────────────
create table if not exists centros_custo (
    id         bigserial   primary key,
    conta_id   bigint      not null references contas(id) on delete restrict,
    nome       text        not null,
    descricao  text        not null default '',
    cor        text        not null default '',        -- swatch opcional na UI
    ativo      boolean     not null default true,
    ordem      smallint    not null default 0,
    criado_em  timestamptz not null default now()
);
create index if not exists idx_centros_custo_conta on centros_custo (conta_id, ativo);

-- ── Classificação opcional no livro-caixa (nullable → retrocompatível) ───────
alter table lancamentos add column if not exists plano_conta_id  bigint references plano_contas(id)  on delete set null;
alter table lancamentos add column if not exists centro_custo_id bigint references centros_custo(id) on delete set null;
create index if not exists idx_lanc_plano_conta
    on lancamentos (conta_id, plano_conta_id) where plano_conta_id is not null;
create index if not exists idx_lanc_centro_custo
    on lancamentos (conta_id, centro_custo_id) where centro_custo_id is not null;

-- ── Seed do plano padrão (idempotente: reroda sem duplicar) ──────────────────
insert into plano_contas (codigo, nome, grupo, natureza, ordem) values
    -- 1 · RECEITA OPERACIONAL BRUTA
    ('1.1.01', 'Venda de Produtos',                       1, 'receita',  1),
    ('1.1.02', 'Prestação de Serviços',                   1, 'receita',  2),
    ('1.1.03', 'Comissões e Repasses',                    1, 'receita',  3),
    ('1.2.01', 'Aluguéis Ativos',                         1, 'receita',  4),
    ('1.2.02', 'Receitas Financeiras',                    1, 'receita',  5),
    ('1.2.03', 'Outras Receitas',                         1, 'receita',  6),
    -- 2 · DEDUÇÕES DA RECEITA
    ('2.1.01', 'Impostos sobre Vendas (Simples/ISS/ICMS)',2, 'despesa',  7),
    ('2.1.02', 'Devoluções e Cancelamentos',              2, 'despesa',  8),
    ('2.1.03', 'Descontos Concedidos',                    2, 'despesa',  9),
    -- 3 · CUSTOS (CMV / CSP)
    ('3.1.01', 'Custo das Mercadorias Vendidas',          3, 'despesa', 10),
    ('3.1.02', 'Custo dos Serviços Prestados',            3, 'despesa', 11),
    ('3.1.03', 'Insumos e Materiais',                     3, 'despesa', 12),
    -- 4 · DESPESAS COM PESSOAL
    ('4.1.01', 'Salários e Ordenados',                    4, 'despesa', 13),
    ('4.1.02', 'Encargos (INSS / FGTS)',                  4, 'despesa', 14),
    ('4.1.03', 'Pró-labore',                              4, 'despesa', 15),
    ('4.1.04', 'Benefícios (VT / VR / Saúde)',            4, 'despesa', 16),
    ('4.1.05', 'Comissões e Bonificações',                4, 'despesa', 17),
    -- 5 · DESPESAS OPERACIONAIS
    ('5.1.01', 'Aluguel e Condomínio',                    5, 'despesa', 18),
    ('5.1.02', 'Água, Luz, Internet e Telefone',          5, 'despesa', 19),
    ('5.1.03', 'Marketing e Publicidade',                 5, 'despesa', 20),
    ('5.1.04', 'Materiais de Escritório',                 5, 'despesa', 21),
    ('5.1.05', 'Manutenção e Limpeza',                    5, 'despesa', 22),
    ('5.1.06', 'Software e Assinaturas',                  5, 'despesa', 23),
    ('5.1.07', 'Contabilidade e Consultorias',            5, 'despesa', 24),
    ('5.1.08', 'Viagens e Deslocamentos',                 5, 'despesa', 25),
    -- 6 · DESPESAS FINANCEIRAS
    ('6.1.01', 'Juros e Multas',                          6, 'despesa', 26),
    ('6.1.02', 'Tarifas Bancárias',                       6, 'despesa', 27),
    ('6.1.03', 'Taxas de Cartão / Maquininha',            6, 'despesa', 28),
    -- 7 · NÃO OPERACIONAL / INVESTIMENTOS
    ('7.1.01', 'Imobilizado (Máquinas / Móveis)',         7, 'despesa', 29),
    ('7.1.02', 'Distribuição de Lucros',                  7, 'despesa', 30),
    ('7.1.03', 'Empréstimos e Financiamentos',            7, 'despesa', 31)
on conflict (codigo) do nothing;

-- rollback (manual):
--   alter table lancamentos drop column if exists plano_conta_id;
--   alter table lancamentos drop column if exists centro_custo_id;
--   drop table if exists centros_custo;
--   drop table if exists plano_conta_habilitada;
--   drop table if exists plano_contas;
