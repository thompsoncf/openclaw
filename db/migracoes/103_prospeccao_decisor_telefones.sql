-- 103_prospeccao_decisor_telefones.sql
-- Lista COMPLETA de telefones do decisor (Credify), pra ficha mostrar todos com
-- tipo + marca de WhatsApp (não só o principal). Cada item:
--   {"formatado": "(86) 99988-0541", "tipo": "COMERCIAL", "whatsapp": true}
-- Idempotente.

alter table public.prospeccao add column if not exists decisor_telefones jsonb;
