-- 108_campanha_material_tipo.sql
-- Tipo do material da campanha: define o que o lead recebe ao clicar "Tenho
-- interesse". Valores: 'link' | 'video' | 'pdf' | 'foto'. O conteúdo em si
-- (URL do link/vídeo ou URL pública do arquivo subido) continua em `material`.
-- Idempotente.

alter table public.campanhas add column if not exists material_tipo text default 'link';
