-- 131_evento_link_online.sql
-- Link da chamada (Meet/Zoom/Teams) quando o compromisso é online. Separado do
-- `local` de propósito: `local` continua só o marcador "Online" usado pra
-- detectar que não tem endereço físico (ver eh_online() em finance/agenda.py) —
-- misturar o link ali quebraria essa detecção. Opcional, sem validação de
-- formato. Idempotente.

alter table public.eventos_agenda add column if not exists link_online text;
