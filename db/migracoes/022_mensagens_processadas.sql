create table if not exists mensagens_processadas (
    chave      text primary key,
    criado_em  timestamptz not null default now()
);
comment on table mensagens_processadas is
    'Idempotencia para Telegram + WhatsApp: chave = tg:update_id ou wa:MessageSid';
