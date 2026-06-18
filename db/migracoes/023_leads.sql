create table if not exists leads (
    id           bigserial primary key,
    canal        text not null,            -- 'whatsapp' | 'telegram'
    identificador text not null,           -- numero (wa) ou telegram_id (tg) como texto
    gastos_usados int not null default 0,
    virou_conta  boolean not null default false,
    conta_id     bigint,                   -- preenchido se o lead cadastrar
    criado_em    timestamptz not null default now(),
    ultimo_em    timestamptz not null default now(),
    unique (canal, identificador)
);
