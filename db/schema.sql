-- Esquema do OpenClaw (multi-tenant: contas + membros).
-- Regra de ouro: TODO dado de dominio carrega a conta (conta_id).

-- Estas tabelas sao criadas pela migracao 001; o schema.sql so' garante
-- que elas existam em instalacoes do zero (sem rodar a migracao).

create table if not exists contas (
    id                   bigserial primary key,
    tipo                 text not null check (tipo in ('pf','pj')),
    nome                 text not null,
    documento            text,
    razao_social         text,
    email                text,
    plano                text,
    status               text not null default 'trial'
                         check (status in ('trial','ativa','inadimplente','suspensa','cancelada')),
    vencimento           date,
    limite_mensagens_dia int not null default 50,
    criado_em            timestamptz not null default now()
);

create table if not exists membros (
    id          bigserial primary key,
    conta_id    bigint not null references contas(id) on delete cascade,
    nome        text,
    papel       text not null default 'membro' check (papel in ('dono','membro')),
    telegram_id bigint unique,
    whatsapp_id text unique,
    ativo       boolean not null default true,
    criado_em   timestamptz not null default now()
);
create index if not exists idx_membros_conta on membros (conta_id);

create table if not exists planos (
    codigo                 text primary key,
    nome                   text not null,
    tipo_conta             text not null check (tipo_conta in ('pf','pj')),
    preco_base_centavos    int not null,
    membros_inclusos       int not null default 1,
    preco_assento_centavos int not null default 0,
    ativo                  boolean not null default true,
    criado_em              timestamptz not null default now()
);

create table if not exists modulos (
    codigo         text primary key,
    nome           text not null,
    preco_centavos int not null default 0,
    ativo          boolean not null default true,
    criado_em      timestamptz not null default now()
);

create table if not exists conta_modulos (
    conta_id  bigint not null references contas(id) on delete cascade,
    modulo    text not null references modulos(codigo),
    ativo     boolean not null default true,
    criado_em timestamptz not null default now(),
    primary key (conta_id, modulo)
);

create table if not exists lancamentos (
    id             bigserial primary key,
    conta_id       bigint not null references contas(id) on delete cascade,
    membro_id      bigint references membros(id) on delete set null,
    tipo           text not null check (tipo in ('despesa', 'receita')),
    valor_centavos bigint not null check (valor_centavos >= 0),
    categoria      text not null,
    descricao      text not null default '',
    data           date not null,
    pagamento      text not null default '',
    origem         text not null default 'manual',
    comprovante    text not null default '',
    criado_em      timestamptz not null default now()
);
create index if not exists idx_lanc_conta on lancamentos (conta_id);
create index if not exists idx_lanc_data  on lancamentos (conta_id, data);

create table if not exists itens_lancamento (
    id                     bigserial primary key,
    lancamento_id          bigint not null references lancamentos(id) on delete cascade,
    descricao              text not null,
    quantidade             numeric(12,3) not null default 1,
    valor_unitario_centavos int not null default 0,
    valor_total_centavos   int not null default 0,
    criado_em              timestamptz not null default now()
);
create index if not exists idx_itens_lancamento on itens_lancamento (lancamento_id);

create table if not exists uso_diario (
    conta_id  bigint not null references contas(id) on delete cascade,
    dia       date   not null,
    mensagens int    not null default 0,
    primary key (conta_id, dia)
);

create table if not exists eventos_conta (
    id        bigserial primary key,
    conta_id  bigint not null references contas(id) on delete cascade,
    membro_id bigint references membros(id) on delete set null,
    tipo      text not null,
    detalhe   text not null default '',
    criado_em timestamptz not null default now()
);
create index if not exists idx_eventos_conta on eventos_conta (conta_id);

-- Catalogo inicial (pode ser editado depois)
insert into planos (codigo, nome, tipo_conta, preco_base_centavos, membros_inclusos, preco_assento_centavos)
values
  ('pf_individual', 'PF Individual', 'pf', 2990, 1, 0),
  ('pf_familia',    'PF Familia',    'pf', 4990, 4, 0),
  ('pj_base',       'PJ Base',       'pj', 9990, 3, 2990)
on conflict (codigo) do nothing;

insert into modulos (codigo, nome, preco_centavos) values
  ('financeiro', 'Modulo Financeiro', 0),
  ('contador',   'Modulo Contador',   1990)
on conflict (codigo) do nothing;
