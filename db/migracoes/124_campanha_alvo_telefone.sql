-- 124_campanha_alvo_telefone.sql
-- Trava o número de WhatsApp escolhido na hora de "jogar" o lead pra campanha
-- (Base: checkbox por número do decisor, o ⭐ mais provável já vem marcado).
-- Nulo = comportamento de sempre (o motor escolhe sozinho em tempo de envio,
-- ver finance/campanhas_motor._numero_alvo_wa).

alter table public.campanha_alvos add column if not exists alvo_telefone text;
