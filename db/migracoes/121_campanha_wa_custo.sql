-- 121_campanha_wa_custo.sql
-- Custo real do WhatsApp por lead da campanha. A Meta manda a CATEGORIA
-- (marketing/utility/authentication/service) e se é COBRÁVEL no webhook de status
-- (objeto pricing) — o valor em R$ a gente calcula pela tarifa oficial do Brasil
-- (finance/wa_precos.py). No envio já gravamos uma estimativa (marketing); quando o
-- status da Cloud API chega com o pricing real, corrigimos (ex.: FEP = grátis).

alter table public.campanha_alvos add column if not exists wa_categoria text;
alter table public.campanha_alvos add column if not exists wa_cobravel boolean;
alter table public.campanha_alvos add column if not exists wa_custo numeric(10,4);
