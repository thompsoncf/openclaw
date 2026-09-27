-- 403_resgate_origem_e_espelho.sql
-- O MOCKUP DAS TRÊS TRILHAS, versão 2 (docs/mockups/funil_tres_trilhas.html), aprovado
-- pelo dono em 27/09/2026 "com as recomendações".
--
-- 1. `resgate_leads` guarda DE ONDE o lead veio e o que a IA entendeu antes de chamar:
--      origem        'follow_up' (parado com o vendedor), 'perdido_vendedor',
--                    'perdido_esteira' (fechado "sem tratativa") ou 'ia_numero' (o
--                    perdido da IA do número que volta depois de 30 dias, uma vez);
--      parado_desde  o marco do relógio quando passou (o "9 dias parado" do card);
--      perda_motivo  o motivo da perda, quando veio dos perdidos;
--      uma_vez       chamado uma vez só, sem o 2º e o 3º toque (o perdido "não
--                    respondeu" e a repescagem da IA);
--      resumo_linha  a linha do ✨ Resumo que o card da coluna Resgate mostra.
--    Linha antiga fica com origem nula — a tela diz só "Resgate".
-- 2. `funil_regua` ganha o ESPELHO DO VENDEDOR: `espelho_de` (o vendedor copiado) e
--    `espelho_para` (quem recebe a cópia no WhatsApp). Um vendedor por vez. Nasce
--    vazio: sem os dois, nada muda.
--
-- Não toca em `canais_config` nem em nada da conexão (CLAUDE.md §1). Aditiva e
-- idempotente; tabelas pequenas, colunas sem reescrita.

alter table if exists public.resgate_leads
  add column if not exists origem        text,
  add column if not exists parado_desde  timestamptz,
  add column if not exists perda_motivo  text,
  add column if not exists uma_vez       boolean not null default false,
  add column if not exists resumo_linha  text;

alter table if exists public.funil_regua
  add column if not exists espelho_de   bigint,
  add column if not exists espelho_para bigint;

-- rollback:
--   alter table public.resgate_leads drop column if exists origem,
--     drop column if exists parado_desde, drop column if exists perda_motivo,
--     drop column if exists uma_vez, drop column if exists resumo_linha;
--   alter table public.funil_regua drop column if exists espelho_de,
--     drop column if exists espelho_para;
