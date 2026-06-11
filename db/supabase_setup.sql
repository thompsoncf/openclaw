-- OpenClaw Schema para Supabase
-- Cole tudo isso no SQL Editor do Supabase e clique em Run

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

-- RLS: tranca tudo pro app (conexao direta via DB_URL tem acesso total)
alter table usuarios enable row level security;
alter table lancamentos enable row level security;
alter table uso_diario enable row level security;

-- Politicas RLS vazias (nega tudo via API publica Supabase)
create policy "usuarios_deny" on usuarios for all using (false);
create policy "lancamentos_deny" on lancamentos for all using (false);
create policy "uso_diario_deny" on uso_diario for all using (false);
