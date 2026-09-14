-- 258_novidade_trava_insistencia.sql
-- O aviso da trava da insistência (CLAUDE.md §5).
--
-- Agora a tela MUDA: quando a empresa liga a trava, o vendedor encontra o bloco do
-- motivo grudado no campo de escrever. Por isso este PR leva aviso, e o do ensaio
-- (257) não levava — lá nada aparecia pra ninguém.
--
-- PÚBLICO 'todos' (§6): insistir com cliente que não responde é de qualquer ramo.
-- O texto não nomeia festa, visita nem mensalidade, e o prazo que ele cita é o que
-- cada empresa configurou na etapa dela.
--
-- PRA QUEM: vendedor em primeiro lugar — é a tela dele, e é ele quem escolhe o
-- motivo. Dono e gestor entram porque a chave é deles e porque precisam saber que,
-- com ela ligada, a equipe vai bater nesse bloco.
--
-- NASCE DESLIGADA em toda conta. O aviso diz isso na primeira linha do corpo: um
-- aviso que descreve uma tela que ninguém tem ainda ensina a ignorar avisos.
--
-- Aditiva e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('trava-da-insistencia', 'novidade', 'todos', '{vendedor,gestor,dono}',
 'A empresa pode pedir um motivo antes do quarto "oi, tudo bem?"',
 'Quando o prazo da etapa estoura e o cliente não respondeu, o app pode pedir um motivo antes de mandar outra mensagem — e o motivo escolhido já marca o retorno, move o lead ou renova o prazo.',
 '/cockpit',
 $txt$Isto nasce DESLIGADO. Nada muda no seu app até a sua empresa ligar, e quem liga é o dono ou o gestor, na Régua do funil.

O que acontece quando está ligado:

Se o prazo da etapa estourou E a última mensagem foi sua, o app pede um motivo antes de você mandar outra. A mensagem vai normalmente — é um toque a mais, não uma porta fechada.

Você escolhe entre quatro, e cada um FAZ alguma coisa:

"Ele pediu para eu chamar nesta data" — você põe o dia, e o lead sai da sua fila até lá.

"Estou mandando o que ele pediu" — o lead anda para a próxima etapa do funil sozinho.

"Vou tentar por outro caminho" — o prazo é renovado e você segue.

"Outro" — pede uma linha contando o que houve.

Três coisas que valem saber.

QUEM ESTÁ ESPERANDO RESPOSTA NUNCA TRAVA. Se o cliente escreveu por último, o campo abre normal — não importa há quantos dias, nem quantas vezes você já tinha tentado antes. A trava é contra insistir no vazio, nunca contra responder alguém.

Justificar uma vez vale por um período inteiro. Não é a cada mensagem: você justifica, o prazo renova, e dentro dele o app não pergunta mais nada sobre aquele lead.

Na terceira vez não há motivo que passe. Esgotadas as renovações da etapa, o app oferece as duas saídas que sobraram — mover o lead de etapa, ou marcar como perdido com o motivo. Insistir pela sexta vez em quem não respondeu cinco não muda o resultado.$txt$,
 timestamptz '2026-09-14 20:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'trava-da-insistencia';
