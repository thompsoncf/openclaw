-- 010_qr_leituras.sql
-- Auditoria de leitura de QR em fotos/PDFs de NFC-e
-- Rastreia: qual conta enviou, conseguiu ler ou nao, data/UF/CNPJ do emitente

create table qr_leituras (
    id bigserial primary key,
    conta_id bigint not null references contas(id) on delete cascade,
    chave varchar(44),                    -- null se nao leu QR
    uf varchar(2),                        -- extraido da chave se leu
    cnpj_emitente varchar(14),            -- extraido da chave se leu
    data_emissao date,                    -- extraido da chave se leu
    media_type varchar(30),               -- image/jpeg, application/pdf, etc
    leu boolean default false,            -- true se conseguiu ler (chave nao null)
    criado_em timestamp default now()
);

create index ix_qr_leituras_conta_id on qr_leituras(conta_id);
create index ix_qr_leituras_criado_em on qr_leituras(criado_em);
