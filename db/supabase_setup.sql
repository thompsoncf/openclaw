-- ============================================================
-- OpenClaw — setup do banco para o Supabase
-- Cole TODO este conteudo no SQL Editor do Supabase e clique em "Run".
-- (Alternativa: rodar init_schema() pelo codigo — faz o mesmo.)
-- ============================================================

-- 1) TABELAS ---------------------------------------------------

create table if not exists usuarios (
    id                   bigserial primary key,
    telegram_id          bigint unique not null,
    nome                 text,
    criado_em            timestamptz not null default now(),
    limite_mensagens_dia int not null default 50,
    ativo                boolean not null default true
);

create table if not exists lancamentos (
    id             bigserial primary key,
    usuario_id     bigint not null references usuarios(id) on delete cascade,
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

create index if not exists idx_lanc_usuario on lancamentos (usuario_id);
create index if not exists idx_lanc_data    on lancamentos (usuario_id, data);

create table if not exists uso_diario (
    usuario_id bigint not null references usuarios(id) on delete cascade,
    dia        date   not null,
    mensagens  int    not null default 0,
    primary key (usuario_id, dia)
);

-- 2) SEGURANCA (importante no Supabase) -----------------------
-- O Supabase publica uma API REST publica em cima das tabelas. Ligar o RLS
-- SEM criar politicas tranca essa API: ninguem acessa pela URL publica.
-- O nosso app continua funcionando porque conecta direto no Postgres
-- (string de conexao / pooler), que ignora o RLS. Ou seja: dado financeiro
-- so' entra/sai pelo nosso codigo, nunca pela API publica do Supabase.

alter table usuarios    enable row level security;
alter table lancamentos enable row level security;
alter table uso_diario  enable row level security;

-- Pronto. As tabelas estao criadas e protegidas.
