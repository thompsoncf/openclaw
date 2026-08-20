-- 180_novidade_agenda_pendencias.sql
-- A agenda passou a cobrar conferência — e ganhou o vocabulário de quem vende data.
--
-- Seguindo a receita da 175:
--   PORTÃO  → 'eventos'. É `vende_data` que decide o card na tela (web/painel_agenda),
--             e são os tipos de festa que mudaram. Pra clínica, loja e escritório
--             dois compromissos no mesmo dia é a rotina, não um alerta — nada muda lá.
--   TIPO    → 'novidade'. Ninguém perdeu nada: a agenda ganhou um aviso que não tinha
--             e três palavras que faltavam. Marca lida ao abrir a tela.
--   QUEM LÊ → dono e gestor. O card manda conferir COM os vendedores, e quem
--             distribui essa conversa é quem manda.
--
-- Aditivo e idempotente.
insert into public.novidades (chave, tipo, publico, titulo, corpo, publicado_em) values

('agenda-pendencias-e-tipos', 'novidade', 'eventos',
 'A agenda avisa quando duas festas caem no mesmo dia',
 $txt$Um card novo no topo da Agenda junta o que precisa de conferência humana. Ele some sozinho quando você zera.

TRÊS COISAS ELE MOSTRA

Duas festas no mesmo dia. A agenda já sabia avisar choque de HORÁRIO, e isso não servia pra quem aluga espaço: uma locação às 17h e outra às 20h no mesmo sábado não se sobrepõem por hora nenhuma, e são duas festas no mesmo salão. Agora o dia inteiro é a unidade. A pré-reserva conta junto — é justamente a data segurada que ninguém lembra que está ocupada.

Horário que o sistema chutou. Quando a data entra sem hora definida, a agenda mostra o horário sublinhado e põe a linha nessa lista, até alguém confirmar. Palpite nunca fica com a mesma cara de dado escolhido.

Festa sem vendedor. Compromisso futuro que não tem ninguém responsável é festa que ninguém está tocando — e o pior caso é uma negociação em aberto sem quem feche.

O card não bloqueia nada. Ele avisa, e você resolve com a equipe: com dois salões duas festas cabem, e quem sabe se cabe é você.

DUAS COISAS A MAIS

Os tipos de festa ganharam Locação, Formatura e Buffet, que faltavam na lista.

E dá pra segurar uma data SEM prazo correndo. Antes, segurar exigia dar um prazo — e prazo vencido libera a data sozinho, o que não serve pra uma negociação de casamento marcada pra daqui a nove meses. Agora o prazo é opcional: você põe quando quiser apertar o cliente.$txt$,
 timestamptz '2026-08-19 21:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'agenda-pendencias-e-tipos';
