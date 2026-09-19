-- 294_novidade_fecho_do_dia.sql
-- O fecho do dia pra quem decide, e o fim do aviso em dobro.
--
-- O QUE MUDOU:
--   * DONO e GESTOR passam a receber, depois que a janela de atendimento fecha, o
--     placar do dia: quantos leads foram tratados e quantos continuam na esteira,
--     por vendedor. Por e-mail e WhatsApp, uma vez por dia, só em dia que teve
--     cobrança.
--   * O AVISO EM DOBRO ACABOU. Onde a esteira está ligada, o follow-up para de
--     mandar a cobrança dele. A fila continua na tela igual.
--   * O E-MAIL de todos esses avisos ganhou botão, e o link do WhatsApp passou a
--     cair na fila do que venceu em vez do painel inteiro.
--
-- POR QUE. Em 19/09/2026 a Prime passou o dia com os dois motores ligados: o
-- follow-up cobrou às 08:00 em três canais e a esteira cobrou OS MESMOS leads às
-- 09:19 em dois — cinco avisos por vendedor no mesmo dia. E a medição daquele dia
-- mostrou o outro lado: dos 30 leads cobrados no dia anterior, ZERO tiveram
-- mensagem, resposta ou movimento em 24 horas. Cobrança que ninguém confere vira
-- ritual.
--
-- O CUIDADO QUE O TEXTO CARREGA: ligação por telefone e conversa pessoal não
-- aparecem no sistema. O placar é factual e sem adjetivo, e diz isso em uma linha
-- — um aviso que chama de relapso quem resolveu no telefone perde a equipe no
-- primeiro dia.
--
-- PRA QUEM: dono e gestor. O vendedor não muda de rotina: ele já recebe o aviso
-- da esteira, com o resumo do que ficou de ontem.
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('fecho-do-dia', 'novidade', 'servico', '{dono,gestor}',
 'No fim do dia você recebe o placar da cobrança, por vendedor',
 'Depois que a janela de atendimento fecha, dono e gestor recebem quantos leads foram tratados e quantos continuam na esteira, vendedor por vendedor. E o aviso em dobro acabou: onde a esteira está ligada, só ela cobra.',
 '/painel/follow-up',
 $txt$A esteira cobra o vendedor de manhã. Agora ela presta contas à noite.

O QUE VOCÊ VAI RECEBER

Depois que a janela de atendimento fecha:

  📋 O dia fechou: 3 tratados, 7 na esteira

  · THIAGO — 1 tratados, 4 na esteira
  · PEDRO YAN — 2 tratados, 3 na esteira

Por e-mail e WhatsApp. Uma vez por dia, e só em dia que teve cobrança — aviso que chega dizendo nada é o que ensina a ignorar o próximo. Sem push: é leitura de fim de dia, não interrupção.

O AVISO EM DOBRO ACABOU

Havia dois motores cobrando os mesmos leads: o follow-up de manhã e a esteira logo depois. Cinco avisos por vendedor no mesmo dia, dois deles dizendo a mesma coisa.

Agora, onde a esteira está ligada, a cobrança é dela. O follow-up continua calculando a fila, a próxima ação e a tela — só parou de mandar mensagem por cima.

O E-MAIL GANHOU BOTÃO

Era o único canal de onde não dava pra chegar a lugar nenhum: o WhatsApp leva link, o push abre no toque, e o e-mail pedia pra você ir procurar a tela. Agora tem botão, e o link cai na fila do que venceu.

O QUE O PLACAR NÃO ENXERGA

Conta como tratado: mensagem nossa (inclusive a que sai pelo WhatsApp Web, sem passar pelo sistema), o card movido à mão, ou o cliente voltando a falar.

NÃO conta ligação por telefone nem conversa pessoal. Se o vendedor resolveu no telefone, o placar vai dizer que ele não tratou, e estará errado sobre ele. É por isso que o texto não tem adjetivo: é um número pra conversar em cima, não um julgamento.$txt$,
 timestamptz '2026-09-19 15:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'fecho-do-dia';
