-- Identidade do fornecedor (loja)
alter table contas add column if not exists logo_url text;
alter table contas add column if not exists bio text;            -- 1 linha
alter table contas add column if not exists sobre text;          -- paragrafo
alter table contas add column if not exists whatsapp_loja text;  -- contato da loja
alter table contas add column if not exists verificado boolean not null default false; -- selo (admin)

-- Recursos iFrutus
alter table contas add column if not exists desconto_pix_pct numeric(5,2);          -- ex: 7.00
alter table contas add column if not exists frete_gratis_acima_centavos bigint;     -- ex: 8000
alter table catalogo_produtos add column if not exists sazonal boolean not null default false; -- "Ta na epoca"
