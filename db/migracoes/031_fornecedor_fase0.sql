-- ============================================================
-- Migracao 031 — FASE 0 Zaq Fornecedor: fundacao
-- ============================================================
-- Adiciona FORNECEDOR + NICHO + modelo CLIENTE-CENTRICO de
-- vinculos + ESTRUTURA pra dados fiscais (preenchida depois).
-- Idempotente. Zero impacto no que já existe.
-- ============================================================

-- ---- 1. NICHOS (hortifruti, padaria, acougue, pet, servicos...) ----
create table if not exists nichos (
    id        bigserial primary key,
    nome      text not null,
    slug      text not null unique,
    tipo      text not null default 'produto'   -- 'produto' (vende mercadoria) | 'servico'
              check (tipo in ('produto','servico')),
    ativo     boolean not null default true,
    criado_em timestamptz not null default now()
);

insert into nichos (nome, slug, tipo)
select 'Hortifruti', 'hortifruti', 'produto'
where not exists (select 1 from nichos where slug = 'hortifruti');

-- ---- 2. FORNECEDOR: flag + acordo comercial na conta ----
-- (o fornecedor é uma conta PJ existente; só ganha status + acordo)
alter table contas add column if not exists eh_fornecedor   boolean not null default false;
alter table contas add column if not exists nicho_id        bigint references nichos(id);
alter table contas add column if not exists comissao_pct    numeric(5,2);   -- ex: 10.00
alter table contas add column if not exists asaas_wallet_id text;
alter table contas add column if not exists fornecedor_slug text unique;    -- pra loja /f/{slug}

-- ---- 3. DADOS FISCAIS do fornecedor (tabela PROPRIA) ----
-- Estrutura criada AGORA (pronta pros 2 casos: fornecedor emite OU Zaq emite).
-- Na Fase 0, só o MINIMO é preenchido. Os campos pesados (certificado, CNAE, etc.)
-- ficam opcionais e são preenchidos na FASE FISCAL propria, quando decidir quem emite.
-- Tabela separada de 'contas' pra não poluir a conta (que todo mundo usa).
create table if not exists fornecedor_fiscal (
    conta_id            bigint primary key references contas(id),
    -- MINIMO (Fase 0 — o fornecedor preenche na tela dele):
    razao_social        text,
    cnpj                text,
    endereco            text,
    -- FISCAL COMPLETO (preenchido na fase fiscal — opcional agora):
    inscricao_estadual  text,        -- IE (comercio/ICMS); servico pode ser isento
    inscricao_municipal text,        -- IM (servico/ISS)
    regime_tributario   text         -- 'simples' | 'simples_excesso' | 'presumido' | 'real'
                        check (regime_tributario in ('simples','simples_excesso','presumido','real')),
    cnae                text,        -- atividade economica
    -- EMISSAO (preenchido só se/quando o Zaq emitir pelo fornecedor):
    quem_emite          text default 'indefinido'   -- 'fornecedor' | 'zaq' | 'indefinido'
                        check (quem_emite in ('fornecedor','zaq','indefinido')),
    certificado_ref     text,        -- referencia ao certificado (NUNCA a senha/arquivo aqui)
    emissor_integrado   text,        -- ex: 'asaas' | 'nfeio' | null
    atualizado_em       timestamptz not null default now()
);

-- NOTA DE SEGURANCA: senha de certificado e o arquivo .pfx/.p12 NÃO vão no banco.
-- Guardar em cofre/secret (ou no emissor integrado). Aqui só uma referencia.

-- ---- 4. VINCULOS / ASSINATURAS (modelo CLIENTE-CENTRICO) ----
create table if not exists vinculos (
    id            bigserial primary key,
    cliente_id    bigint not null references contas(id),
    fornecedor_id bigint not null references contas(id),
    nicho_id      bigint references nichos(id),
    tipo          text not null default 'assinatura'
                  check (tipo in ('assinatura','avulso')),
    status        text not null default 'ativo'
                  check (status in ('ativo','pausado','cancelado')),
    frequencia    text check (frequencia in ('semanal','quinzenal','mensal')),
    criado_em     timestamptz not null default now()
);

create index if not exists ix_vinculos_cliente    on vinculos (cliente_id);
create index if not exists ix_vinculos_fornecedor on vinculos (fornecedor_id);

-- ============================================================
-- ROLLBACK (rodar manual, com cuidado):
--   drop table if exists vinculos;
--   drop table if exists fornecedor_fiscal;
--   alter table contas drop column if exists eh_fornecedor, drop column if exists nicho_id,
--     drop column if exists comissao_pct, drop column if exists asaas_wallet_id,
--     drop column if exists fornecedor_slug;
--   drop table if exists nichos;
-- ============================================================
