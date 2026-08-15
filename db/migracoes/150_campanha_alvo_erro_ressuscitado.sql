-- Falha de entrega que voltou a contar como sucesso.
--
-- Os callbacks de status do Twilio chegam fora de ordem. Em produção veio, em 3
-- segundos: enviado → erro 63024 → enviado. A trava de "nunca rebaixar" tinha a
-- escada enviado=1, entregue=2, lido=3 e o 'erro' caía no `else 0`; como 0 < 1, o
-- "enviado" atrasado passava por cima e ressuscitava a falha como sucesso.
--
-- Sobrava o `wa_erro_codigo` órfão: o alvo não voltava pra fila de reenvio, o KPI
-- de erros contava a menos e o painel "Números não tentados" nem via o lead —
-- 28 alvos em 4 campanhas, segurando 156 telefones guardados na base.
--
-- O código já não deixa mais isso acontecer (_WA_STATUS_RANK, 'erro' empata com
-- 'enviado'). Aqui a gente conserta o que já ficou torto.
--
-- Só os 63xxx, que são do DESTINATÁRIO (63024 = o número não tem WhatsApp) — é a
-- mesma divisão da 147/148. E só quem parou em 'enviado': quem chegou a
-- 'entregue'/'lido' recebeu de verdade, e aí o erro é que era a informação velha.
--
-- Idempotente: depois da primeira passada o `where` não casa mais nada.
update public.campanha_alvos
   set wa_status = 'erro'
 where wa_status = 'enviado'
   and coalesce(wa_erro_codigo,'') ~ '^63[0-9]{3}$';
