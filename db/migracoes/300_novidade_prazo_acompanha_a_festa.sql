-- 300_novidade_prazo_acompanha_a_festa.sql
-- O prazo da data segurada passou a acompanhar a FESTA, não o relógio de hoje.
--
-- O QUE MUDOU NA TELA:
--   * Ao marcar "Só segurar a data", o campo "segurar até" já vem preenchido com
--     60 dias ANTES da festa, e não mais com "hoje + prazo da conta".
--   * Festa que está logo aí continua com o prazo configurado na conta — a regra
--     nova é um piso, não uma troca.
--   * Data que nasce de orçamento aprovado agora chega na agenda COM o vendedor,
--     em vez de cair no aviso "sem vendedor".
--
-- POR QUE. Medido na Prime (conta 34) em 19/09/2026: NOVE datas foram canceladas
-- sozinhas pelo prazo vencendo. Não foi cliente desistindo — o relógio bateu e o
-- sistema soltou. O prazo dado era sempre de 4 a 6 dias, qualquer que fosse a
-- distância da festa:
--
--     Casamento — Maria Carolina · festa 24/07/27 · venceu 312 dias antes
--     Aniversário — Larissa Rakel · festa 05/02/28 · venceu 505 dias antes
--     Confraternização — Flavia   · festa 03/12/26 · venceu  79 dias antes
--
-- Três dessas o dono ainda dava como negociação viva ou venda fechada: a
-- expiração automática estava DESFAZENDO venda. Cinco dias de relógio numa festa
-- daqui a dez meses vence antes de qualquer cliente pagar sinal.
--
-- E O VENDEDOR: dos nove orçamentos da conta que viraram data, os NOVE nasceram
-- sem vendedor, cada um virando uma linha no card de pendências. O dado nunca
-- faltou — `orcamentos.criado_por` sempre soube quem era; ninguém passava adiante.
--
-- PRA QUEM: dono, gestor e vendedor. Quem segura data é quem sente os dois.
--
-- O PORTÃO: `eventos` — só quem vende data segura data.
--
-- CONTAS ALCANÇADAS: 34 (MANOEL SOARES) e 35 (Louana vanessa cardoso Santos
-- costa).
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('agenda-prazo-acompanha-a-festa', 'mudanca', 'eventos', '{dono,gestor,vendedor}',
 'A data segurada não vence mais antes da hora',
 'O prazo para o cliente pagar o sinal passou a ser calculado a partir da data da festa, e não dos próximos dias: uma festa daqui a dez meses fica segurada até dois meses antes dela, em vez de liberar sozinha em menos de uma semana.',
 '/painel/agenda',
 $txt$Se você já segurou uma data para um cliente e depois viu que ela tinha sumido do calendário sozinha, era isto.

O QUE ACONTECIA

A data ficava segurada por um número fixo de dias — o que está configurado no card "Data segurada". Cinco dias, por exemplo. Passou o prazo sem o sinal, o sistema soltava a data.

Faz sentido para uma festa que é semana que vem. Não faz nenhum para um casamento que é daqui a dez meses: ninguém paga sinal de uma festa de julho do ano que vem em cinco dias. O prazo vencia, a data voltava para o ar, e a negociação continuava viva na cabeça de todo mundo menos na do sistema.

Na sua conta isso aconteceu NOVE vezes. O casamento da Maria Carolina, que é em julho de 2027, foi liberado 312 dias antes da festa. O aniversário da Larissa Rakel, 505 dias antes.

O QUE MUDOU

O prazo agora acompanha a festa:

• Festa longe: a data fica segurada até 60 dias antes dela. Tempo de sobra para o cliente decidir, e dois meses para revender se não der.
• Festa perto: continua o prazo da sua conta, como sempre foi.
• O prazo nunca passa da festa.

Ao marcar "Só segurar a data", o campo de prazo já vem preenchido com a conta nova. Você pode mudar à mão, como antes.

DATA DE ORÇAMENTO APROVADO AGORA CHEGA COM VENDEDOR

Segunda coisa que estava atrapalhando: quando o cliente aprovava o orçamento, a data entrava na agenda sem vendedor nenhum — e ia parar na lista de avisos, pedindo que alguém dissesse de quem era. Isso aconteceu com todas as datas que nasceram de orçamento.

Agora ela chega com o nome de quem fez o orçamento. O aviso só sobra para as datas que realmente não têm dono.

O QUE NÃO MUDOU

As pré-reservas que já estão correndo mantêm o prazo delas. E a configuração do card "Data segurada" continua valendo — ela virou o mínimo, não o teto.$txt$,
 timestamptz '2026-09-20 12:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'agenda-prazo-acompanha-a-festa';
