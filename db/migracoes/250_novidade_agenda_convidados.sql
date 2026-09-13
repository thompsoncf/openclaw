-- 250_novidade_agenda_convidados.sql
-- O aviso de que a aba Agenda dos Relatórios trocou a coluna Sinal por convidados
-- na visita (CLAUDE.md §5).
--
-- PÚBLICO 'eventos' (§6): tudo aqui é vocabulário de quem vende data — festa,
-- visita ao espaço, quantidade de convidados, locação. Numa conta de consultoria
-- ou de hortifruti o texto não descreveria nada. Alcança as contas 34 (PRIME
-- EVENTOS) e 35 (DOCE MELL).
--
-- PRA QUEM: dono e gestor, e NÃO vendedor. Relatórios exige a capacidade
-- `financeiro` (web/painel_relatorios.py) — o vendedor não tem a tela, e §5 é
-- explícita: aviso de tela que ele não tem, nunca. A informação chega a ele por
-- outro caminho, o compromisso da agenda, que passa a nascer com a contagem.
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('agenda-convidados-na-visita', 'novidade', 'eventos', '{dono,gestor}',
 'Na Agenda, a visita passa a mostrar a festa e quantos convidados',
 'O relatório de Agenda deixa de exibir a coluna de sinal, quase sempre vazia, e passa a mostrar o tipo de festa e a quantidade de convidados já na aba de visitas.',
 '/painel/relatorios?aba=agenda',
 $txt$Duas colunas da aba Agenda estavam ocupando espaço sem dizer nada, e cada uma pelo seu motivo.

A coluna Sinal saiu. Ela só conseguia se preencher em dois casos: quando alguém usava o "Só segurar a data" ao criar o compromisso, ou quando a data nasceu de um orçamento com pré-reserva. Festa que entra por telefone — que é a maioria — nunca passava por nenhum dos dois, e a coluna mostrava R$ 0,00 em quase toda linha. O valor não sumiu: "Sinal no período" continua no rodapé, somando tudo do período como sempre somou.

A coluna Convidados ficou, e agora enxerga mais longe. Antes ela procurava o número em dois lugares: no campo do próprio compromisso e no orçamento ligado a ele. Agora procura também no lead — quem atendeu o cliente muitas vezes já anotou quantas pessoas vêm, e esse número ficava guardado sem chegar até aqui.

A aba Visitas ganhou duas colunas: Festa e Convid. Até agora a lista de visitas dizia só quem vem e quando. Quem ia receber a pessoa não via na tela que era uma formatura de 100 convidados, mesmo quando o sistema já sabia. Agora vê.

E a visita marcada pelo aplicativo do vendedor já nasce com a contagem. Ela é copiada do lead na hora de agendar, então daqui pra frente o número não depende mais de ninguém digitar de novo.

Uma última diferença, pequena mas que muda a leitura: em locação, a coluna de convidados agora mostra "n/a" em vez de um traço. Locação é festa de terceiro no seu espaço, e a casa não conta convidado — o traço parecia uma pendência para preencher, e nunca foi.$txt$,
 timestamptz '2026-09-13 12:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'agenda-convidados-na-visita';
