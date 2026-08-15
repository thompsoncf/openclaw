-- Pra qual NÚMERO o envio atual saiu.
--
-- A fila de números só avançava quando o Twilio recusava na hora da chamada. O
-- caso comum é outro: ele ACEITA (devolve SID) e a entrega falha depois, por
-- webhook — 63024, "este número não tem WhatsApp". Aí quem marcava `erro` era o
-- webhook, que não mexia em wa_tentativas nem em wa_tentados.
--
-- Resultado visto em produção: o alvo voltava pra fila e o motor mandava pro
-- MESMO número de novo, rodada após rodada, cada uma uma mensagem cobrada.
--
-- O webhook não tinha como saber qual número tentar riscar: o alvo guardava só o
-- wa_sid. Esta coluna fecha essa lacuna — o disparo grava o número junto do SID,
-- e o webhook consegue registrar o que falhou e passar pro próximo.
alter table public.campanha_alvos
  add column if not exists wa_numero text;
