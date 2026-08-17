-- O teto de gasto não enxergava as retentativas.
--
-- `campanhas.teto_wa` é conferido como `sum(wa_custo)` dos alvos da campanha, e o
-- disparo gravava o custo com `wa_custo = coalesce(wa_custo, <estimativa>)`. O
-- coalesce estava lá por um bom motivo: o webhook de status da Meta corrige a
-- estimativa com o preço real, e o envio não podia passar por cima disso.
--
-- Só que a fila de números manda ATÉ 3 mensagens pro mesmo alvo, uma por telefone.
-- Da segunda em diante o coalesce achava um valor já gravado e não somava nada — a
-- mensagem saía, era cobrada, e o teto não via. Um alvo que esgota a fila custa 3
-- mensagens e registrava 1.
--
-- Visto em produção: 17 mensagens disparadas numa tarde, R$ 0,00 somados ao gasto.
--
-- `wa_custo` passa a ser o ACUMULADO do alvo (soma das mensagens que saíram pra
-- ele), e esta coluna guarda quanto custou a ÚLTIMA — que é a parcela que o webhook
-- tem direito de corrigir. Corrigir vira trocar a parcela:
--     wa_custo = wa_custo - wa_custo_msg + <real>
-- Assim o total do alvo continua certo e o preço real da Meta ainda entra.
--
-- Sem backfill: o que já foi cobrado e não foi registrado não dá pra reconstruir a
-- partir daqui (o alvo guarda um valor só, não o histórico). O acumulado passa a
-- valer dos próximos envios em diante.
alter table public.campanha_alvos
  add column if not exists wa_custo_msg numeric(10,4);

-- Alvo que só teve UMA mensagem: o acumulado é a própria parcela. Deixa os dois
-- campos coerentes pra primeira correção de webhook que chegar depois daqui não
-- subtrair um nulo e zerar o custo do alvo.
update public.campanha_alvos
   set wa_custo_msg = wa_custo
 where wa_custo is not null
   and wa_custo_msg is null;
