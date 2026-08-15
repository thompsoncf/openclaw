-- 156_render_evento_deactivated_neutro.sql
-- `deactivated` não é sucesso: é aposentadoria.
--
-- Um deploy fica `deactivated` quando o PRÓXIMO sobe e toma o lugar dele. Ou
-- seja: TODO deploy bem-sucedido acaba desativado mais cedo ou mais tarde.
-- Contando isso como sucesso, cada deploy entrava DUAS vezes no "deu certo" —
-- uma no `live`, outra na aposentadoria — e a taxa de sucesso por serviço,
-- aquela da doc, saía com o dobro do numerador:
--
--     select servico_nome,
--            count(*) filter (where sucesso) as ok, ...
--
-- O `live` do mesmo deploy já contabilizou o acerto. A aposentadoria não é um
-- segundo acerto, nem uma falha — é ausência de veredito, igual a `canceled`.
--
-- O código já trata assim (core/render_eventos.py, _TEXTO_NEUTRO). Esta
-- migração corrige o que ficou gravado antes.
--
-- ALVO: só `deploy_ended` com status `deactivated`. Não encosta em `live`, em
-- falha, nem em linha de outro tipo — essas a 155 já resolveu.
--
-- O `status` FICA: "deactivated" é informação verdadeira e útil (diz que
-- aquele deploy saiu do ar). O que estava errado era só o veredito.
--
-- Idempotente.

update public.render_evento
   set sucesso = null
 where tipo = 'deploy_ended'
   and lower(coalesce(status, '')) = 'deactivated'
   and sucesso is not null;

-- rollback:
--   update public.render_evento set sucesso = true
--    where tipo = 'deploy_ended' and lower(coalesce(status,'')) = 'deactivated';
