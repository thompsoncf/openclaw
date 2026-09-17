-- 272_novidade_agenda_encerramento.sql
-- A agenda mostra a hora que encerra e quantas horas dura.
--
-- O QUE MUDOU NA TELA (/painel/agenda, a caixa que abre ao clicar no dia)
-- 1) O compromisso passa a mostrar a JANELA: "18:00 → 23:00 · 5h". Festa que vira
--    a noite ganha o "+1" — "20:00 → 02:00 +1 · 6h" —, senão quem lê rápido
--    entende que acabou seis horas antes de começar.
-- 2) Sem encerramento, mostra "19:00 → —" e oferece o botão "Informar
--    encerramento", que abre o mesmo formulário de remarcar.
-- 3) A hora que o sistema chutou continua marcada com ⚠️.
--
-- E O CONSERTO QUE IMPORTA MAIS QUE A TELA: a ponte do funil (o compromisso que
-- nasce quando o lead fecha) passa a usar o horário do ORÇAMENTO quando ele
-- existe. Antes ela criava tudo com 19h de palpite e sem fim nenhum.
--
-- A MEDIÇÃO, conta 34, 16/09/2026: das 24 festas marcadas daqui pra frente, ZERO
-- tinham hora de encerramento. Das 66 marcações de empresa, 32 tinham — e eram as
-- VISITAS (30min a 1h) mais as festas vindas da proposta. Das 30 com tipo de
-- evento (Casamento, Locação, Formatura), UMA. Toda visita tinha; toda locação
-- não. A diferença era a porta por onde o compromisso entrou.
--
-- A TRAVA QUE O DADO EXIGIU: o horário só vem do orçamento quando o orçamento
-- fala DA MESMA DATA. A Josiany tem festa em 01/10 e o orçamento dela é de 19/12 —
-- sem comparar, a agenda receberia o horário de outra festa, que é pior que ficar
-- sem horário.
--
-- O QUE O SISTEMA NÃO FAZ, decisão do dono em 17/09: não sugere duração. As festas
-- que têm horário duram 5 a 6 horas, mas sugerir 6h em 24 festas criaria 24
-- números que ninguém conferiu. Quem preenche é a pessoa, pelo botão. E as 24 que
-- já estão lá não foram tocadas.
--
-- O PORTÃO: `eventos`. A janela aparece onde há `fim` (o que já restringe
-- sozinho), mas quem vende mensalidade não tem festa pra ter encerramento, e o
-- pedido saiu do funil de eventos.
--
-- PRA QUEM: dono e gestor. É a agenda da casa; o vendedor vê a dele pelo app.
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('agenda-encerramento-e-horas', 'novidade', 'eventos', '{dono,gestor}',
 'A agenda mostra quando a festa encerra, e quantas horas dura',
 'Ao clicar no dia, cada compromisso agora aparece com a janela inteira — "18:00 → 23:00 · 5h" — e quem ainda não tem horário de encerramento pode informar ali mesmo.',
 '/painel/agenda',
 $txt$Antes a agenda dizia só a hora que a festa começa. Agora ela diz a janela inteira.

O QUE VOCÊ VAI VER

Clicando no dia, cada compromisso mostra "18:00 → 23:00" e a duração do lado: "5h". Quando a festa vira a noite, aparece o "+1" — "20:00 → 02:00 +1 · 6h" — pra não parecer que ela acabou antes de começar.

Meia hora aparece como "30min", e seis horas e meia como "6h30". Nada de "6.5h".

E QUANDO NÃO TEM HORÁRIO

Aí a agenda é honesta: mostra "19:00 → —" e um botão "Informar encerramento" logo abaixo. É o caso da maioria das festas hoje, e tem um motivo.

POR QUE FALTAVA

Quando o lead fecha no funil, o sistema cria o compromisso sozinho. Só que ele criava com 19h de palpite e sem hora de encerramento nenhuma. Já as visitas, que são marcadas à mão, sempre tiveram. Por isso toda visita tinha horário completo e quase nenhuma festa tinha.

Isso foi consertado: agora, quando o lead já tem um orçamento com os horários da festa, o compromisso nasce com a janela inteira — e sem o aviso de "hora sugerida", porque não é palpite.

O QUE O SISTEMA NÃO FAZ

Não inventa duração. A maioria das festas de vocês dura 5 ou 6 horas, mas preencher isso sozinho em dezenas de compromissos criaria dezenas de horários que ninguém conferiu — e alguém marcaria a equipe pelo número errado. Quem informa é você, pelo botão, quando souber.

As festas que já estavam na agenda também não foram mexidas. Elas mostram "→ —" até alguém informar.$txt$,
 timestamptz '2026-09-17 15:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'agenda-encerramento-e-horas';
