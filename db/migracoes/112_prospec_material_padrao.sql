-- 112_prospec_material_padrao.sql
-- Material padrão da conta pro 1º contato: link que o lead recebe ao tocar
-- "Quero o material" QUANDO ele não está em nenhuma campanha (fora de campanha,
-- não há material de campanha pra puxar). É um fallback simples (URL/link).
-- Idempotente.

alter table public.contas add column if not exists prospec_material text;
