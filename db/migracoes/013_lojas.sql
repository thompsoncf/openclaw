-- MIGRACAO 013: tabela de lojas (CNPJ unico) + liga precos a loja.
-- CNPJ (do QR) identifica a filial exata; endereco fica pra exibir ao cliente.
create table if not exists lojas (
    id          bigserial primary key,
    cnpj        text unique not null,
    nome        text,
    endereco    text,
    cidade      text,
    uf          text,
    criado_em   timestamptz not null default now()
);
create index if not exists idx_lojas_cnpj on lojas(cnpj);
alter table precos_observados add column if not exists loja_id bigint references lojas(id) on delete set null;
create index if not exists idx_precos_loja on precos_observados(loja_id);
