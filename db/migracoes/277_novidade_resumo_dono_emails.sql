-- 277_novidade_resumo_dono_emails.sql
-- O resumo semanal ganhou o campo "Seu e-mail" — o que vê os nomes.
--
-- Acompanha a migração 276, e nasce do mesmo defeito: quem cadastrava o próprio
-- e-mail no card do resumo entrava pelo portão de GESTOR, que é a versão SEM o
-- nome de cada vendedor. Quem já ligou o recurso ontem precisa saber disso HOJE,
-- porque o primeiro e-mail sai na segunda.
--
-- O PORTÃO: `servico`, o mesmo da 275 — é a mesma tela, pro mesmo público.
--
-- PRA QUEM: dono e gestor. O card fica na engrenagem da Prospecção, que o vendedor
-- não abre, e o e-mail dele não muda em nada. Aviso de tela que ele não tem, nunca
-- (regra 5, item 2).
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('resumo-semanal-seu-email', 'mudanca', 'servico', '{dono,gestor}',
 'Agora você escolhe quem vê o resultado de cada vendedor',
 'O resumo semanal por e-mail passou a ter dois campos: um pra quem acompanha o resultado de cada vendedor pelo nome, e outro pra quem recebe só o total da equipe.',
 '/painel/prospeccao/comunicacao?aba=agente',
 $txt$Se você ligou o resumo semanal por e-mail ontem, vale um minuto aqui antes da segunda.

O QUE ESTAVA ACONTECENDO: o card tinha um campo só, "E-mails de gestor". E gestor, no resumo, é quem recebe a semana com o TOTAL da equipe — sem o resultado de cada vendedor pelo nome. A versão com os nomes ia só pra quem estivesse cadastrado como dono no sistema, com e-mail no cadastro. Quem não tem e-mail no cadastro — e é o caso de muita gente, porque nunca precisou de um pra entrar no painel — acabava recebendo a versão resumida, mesmo sendo o dono da empresa.

O QUE MUDOU: agora são dois campos.

SEU E-MAIL — quem estiver aqui recebe o resumo completo, com o que cada vendedor recebeu e o que cada um fechou, pelo nome. É onde vai o seu endereço.

E-MAILS DE GESTOR — quem estiver aqui recebe a mesma semana, mas com o total da equipe. É onde vai o sócio, o contador, quem acompanha o andamento sem precisar saber o número de cada pessoa.

O QUE VOCÊ PRECISA FAZER: abra Prospecção → engrenagem → Agente IA e ponha o seu endereço no campo de cima. Se ele estiver no campo de baixo, tire de lá — senão o primeiro resumo chega sem os nomes.

Os dois campos continuam valendo a mesma coisa em uma coisa: e-mail cadastrado ali só recebe o resumo. Não entra no painel, não vê lead, não vê conversa.$txt$,
 timestamptz '2026-09-17 21:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'resumo-semanal-seu-email';
