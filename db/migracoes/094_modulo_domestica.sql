-- 094_modulo_domestica.sql
-- Módulo Doméstica (eSocial Doméstico) — cálculo de CONFERÊNCIA.
-- O empregador doméstico é PESSOA FÍSICA: os lançamentos gerados caem no
-- livro-caixa PESSOAL (natureza='pessoal'), nunca no empresarial. Não há API
-- no eSocial Doméstico — o módulo espelha o cálculo, o fechamento segue no
-- portal gov.br. Cobre qualquer cargo da LC 150/2015 (babá, caseiro, cuidador,
-- motorista particular, cozinheira, jardineiro, diarista mensalista...).
--
-- Só tabelas NOVAS (zero alter em tabela existente): rodar não muda nada pra
-- quem está no ar. Tudo escopado por conta_id (multi-tenant sagrado).

-- ── Vínculo empregatício doméstico ──────────────────────────────────────────
create table if not exists domestica_vinculo (
    id                bigserial primary key,
    conta_id          bigint not null references contas(id) on delete restrict,
    nome              text   not null,
    cpf               text   not null default '',
    cargo             text   not null default '',   -- babá, caseiro, cuidador...
    matricula         text   not null default '',   -- ED001, ED002... (eSocial)
    salario_centavos  int    not null check (salario_centavos >= 0),
    admitido_em       date,
    ativo             boolean not null default true,
    criado_em         timestamptz not null default now()
);
create index if not exists idx_domestica_vinculo_conta
    on domestica_vinculo (conta_id, ativo);

-- ── Folha por competência (o "recibo do mês" + snapshot do cálculo) ─────────
-- Guarda o snapshot em centavos pra o recibo não mudar se a TABELA do motor
-- mudar depois. status: prevista (nosso cálculo) → conferida (bateu com a guia)
-- → paga (baixou no caixa).
create table if not exists domestica_folha (
    id                bigserial primary key,
    conta_id          bigint not null references contas(id) on delete restrict,
    vinculo_id        bigint not null references domestica_vinculo(id) on delete cascade,
    competencia       date   not null,               -- 1º dia do mês de referência
    tabela_ano        int    not null,               -- versão da TABELA usada
    base_inss_centavos           int not null default 0,
    base_fgts_centavos           int not null default 0,
    base_irrf_centavos           int not null default 0,
    inss_empregado_centavos      int not null default 0,
    inss_patronal_centavos       int not null default 0,
    gilrat_centavos              int not null default 0,
    fgts_mensal_centavos         int not null default 0,
    fgts_compensatorio_centavos  int not null default 0,
    irrf_centavos                int not null default 0,
    dae_prevista_centavos        int not null default 0,  -- soma dos tributos da DAE
    liquido_centavos             int not null default 0,
    provisoes_centavos           int not null default 0,  -- 13º + férias + 1/3 c/ encargos
    custo_real_centavos          int not null default 0,  -- desembolso + provisões
    vencimento_dae    date,
    -- conferência: valor da guia oficial e se bateu com o previsto
    dae_conferida_centavos       int,
    status            text   not null default 'prevista'
                      check (status in ('prevista','conferida','paga')),
    lancamento_id     bigint references lancamentos(id) on delete set null,
    criado_em         timestamptz not null default now(),
    unique (conta_id, vinculo_id, competencia)
);
create index if not exists idx_domestica_folha_conta
    on domestica_folha (conta_id, competencia);

-- ── Variáveis do mês (extra, adiant. 13º, faltas, desconto, benefício) ──────
create table if not exists domestica_folha_item (
    id                bigserial primary key,
    conta_id          bigint not null references contas(id) on delete restrict,
    folha_id          bigint not null references domestica_folha(id) on delete cascade,
    tipo              text   not null
                      check (tipo in ('extra','adiantamento_13','falta',
                                      'desconto','beneficio')),
    descricao         text   not null default '',
    valor_centavos    int    not null check (valor_centavos >= 0),
    criado_em         timestamptz not null default now()
);
create index if not exists idx_domestica_folha_item_folha
    on domestica_folha_item (folha_id);

-- ── Módulo no catálogo (liberado por conta via conta_modulos = cortesia/admin) ─
insert into modulos (codigo, nome, preco_centavos)
values ('domestica', 'Módulo Doméstica (eSocial)', 0)
on conflict (codigo) do nothing;
