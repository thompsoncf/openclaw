-- 367_pix_da_empresa.sql
-- A chave Pix da PRÓPRIA empresa, pra cobrar a parcela da reforma com o Pix
-- pronto (finance/pix.py, finance/obra_reforma.py `cobrancas`). Decisão do dono em
-- 26/09/2026: o dinheiro do cliente cai direto na conta da empresa, não no Asaas
-- da ZAQ.
--
--   pix_chave      a chave já normalizada (e-mail, CPF, CNPJ, +55..., aleatória)
--   pix_tipo       email | cpf | cnpj | celular | aleatoria
--   pix_recebedor  o nome que o app do banco mostra; vazio = razão social/fantasia
--   pix_cidade     a cidade do recebedor (campo obrigatório do BR Code); vazio = contas.cidade
--
-- Só o DONO troca (web/painel_obras.py): quem troca a chave troca pra onde vai o
-- dinheiro.
--
-- Aditiva e idempotente.

alter table public.contas add column if not exists pix_chave     text;
alter table public.contas add column if not exists pix_tipo      text;
alter table public.contas add column if not exists pix_recebedor text;
alter table public.contas add column if not exists pix_cidade    text;

-- rollback:
--   alter table public.contas drop column if exists pix_chave, drop column if exists pix_tipo,
--     drop column if exists pix_recebedor, drop column if exists pix_cidade;
