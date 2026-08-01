-- 113_prospec_material_tipo.sql
-- Tipo do material padrão da conta (mesma ideia da campanha): 'link' | 'video' |
-- 'pdf' | 'foto'. O conteúdo (URL do link/vídeo ou URL pública do arquivo subido)
-- continua em contas.prospec_material. Idempotente.

alter table public.contas add column if not exists prospec_material_tipo text default 'link';
