-- Limite separado pra cupons (foto/PDF) - visao custa ~5x o texto
ALTER TABLE contas     ADD COLUMN IF NOT EXISTS limite_cupons_dia int NOT NULL DEFAULT 5;
ALTER TABLE uso_diario ADD COLUMN IF NOT EXISTS cupons            int NOT NULL DEFAULT 0;
