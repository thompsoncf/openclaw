-- Campanha que não diz qual mensagem usou.
--
-- As duas rotas que criam campanha semeavam `_PASSOS_PADRAO` (que É o modelo
-- 'generico') sem gravar `modelo_codigo`. Resultado: 7 das 8 campanhas desta base
-- ficaram órfãs, e o desempenho delas virou número sem causa — não dá pra dizer se
-- 4% de resposta é da mensagem, do nicho ou do horário.
--
-- O código já não deixa mais nascer assim. Aqui a gente recupera o passado, e SÓ
-- onde dá pra provar: a campanha é marcada como 'generico' apenas se os passos dela
-- ainda forem, letra por letra, os do modelo padrão (assunto do D0, do D1 e do D2).
-- Quem editou o texto depois não é mais 'generico' e fica em branco de propósito —
-- inventar a origem é pior que admitir que se perdeu, porque um rótulo errado
-- contamina a comparação que este campo existe pra permitir.
--
-- Idempotente: só toca em quem está com o campo vazio.
update public.campanhas c
   set modelo_codigo = 'generico'
 where coalesce(c.modelo_codigo, '') = ''
   and (select count(*) from public.campanha_passos p where p.campanha_id = c.id) = 3
   and exists (select 1 from public.campanha_passos p
                where p.campanha_id = c.id and p.ordem = 0
                  and p.assunto = 'Uma ideia pra {empresa}' and p.usar_ia)
   and exists (select 1 from public.campanha_passos p
                where p.campanha_id = c.id and p.ordem = 1
                  and p.assunto = 'Só reforçando — vale 2 min?')
   and exists (select 1 from public.campanha_passos p
                where p.campanha_id = c.id and p.ordem = 2
                  and p.assunto = 'Fecho por aqui 👋');
