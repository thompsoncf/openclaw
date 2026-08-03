-- 123_campanha_teto_wa.sql
-- Teto de gasto (em R$) do WhatsApp por campanha. Nulo = sem teto (só mostra o
-- custo previsto). Quando o gasto acumulado alcança o teto, o motor pausa a
-- campanha sozinho (ver finance/campanhas_motor._disparar_wa_campanha).

alter table public.campanhas add column if not exists teto_wa numeric(10,2);
