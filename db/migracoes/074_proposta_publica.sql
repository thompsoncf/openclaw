-- 074_proposta_publica.sql
-- Proposta pública: link pro cliente ver, baixar PDF e aprovar/assinar.
-- - token: código público do link /proposta/<token> (sem login).
-- - itens: snapshot das linhas de módulos no momento de gerar (nome/setup/mensal),
--   pra a proposta ficar estável mesmo que o catálogo mude depois.
-- - aprovada_*: registro da assinatura eletrônica (nome + data/hora + IP).
-- Espelhado em runtime por painel_servicos._garantir_tabela.
alter table orcamentos add column if not exists token        text;
alter table orcamentos add column if not exists itens        jsonb;
alter table orcamentos add column if not exists aprovada_em  timestamptz;
alter table orcamentos add column if not exists aprovada_por text;
alter table orcamentos add column if not exists aprovada_doc text;
alter table orcamentos add column if not exists aprovada_ip  text;

-- novo estado 'aprovada' (cliente assinou; o vendedor ainda fecha o contrato).
alter table orcamentos drop constraint if exists orcamentos_status_check;
alter table orcamentos add constraint orcamentos_status_check
    check (status in ('rascunho','enviado','negociando','aprovada','fechado','perdido'));

create unique index if not exists idx_orcamentos_token
    on orcamentos (token) where token is not null;
