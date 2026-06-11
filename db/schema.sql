-- Esquema do OpenClaw (piloto multi-usuario).
-- Regra de ouro: TODO dado de dominio carrega o dono (usuario_id).

create table if not exists usuarios (
    id                   bigserial primary key,
    telegram_id          bigint unique not null,
    nome                 text,
    criado_em            timestamptz not null default now(),
    -- protecao de custo: teto de mensagens por dia por usuario
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
    origem         text not null default 'manual',   -- 'manual' ou 'foto'
    comprovante    text not null default '',
    criado_em      timestamptz not null default now()
);

create index if not exists idx_lanc_usuario on lancamentos (usuario_id);
create index if not exists idx_lanc_data    on lancamentos (usuario_id, data);

-- Controle de uso diario (protecao de custo do LLM).
create table if not exists uso_diario (
    usuario_id bigint not null references usuarios(id) on delete cascade,
    dia        date   not null,
    mensagens  int    not null default 0,
    primary key (usuario_id, dia)
);
