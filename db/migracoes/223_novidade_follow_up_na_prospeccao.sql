-- 223_novidade_follow_up_na_prospeccao.sql
-- O aviso da mudança de 07/09/2026 no Follow-up (CLAUDE.md §5).
--
-- PÚBLICO 'eventos' (§6): o Follow-up só existe pro perfil de eventos —
-- `finance.follow_up.PERFIS_COM_TELA` é ("eventos",). Mandar isso pra conta que
-- vende por mensalidade seria anunciar uma aba que ela não tem. Quando o
-- recorrente entrar em PERFIS_COM_TELA, ele leva o aviso dele.
--
-- PRA QUEM: dono, gestor E vendedor. É a exceção que a §5 pede que se justifique
-- — o vendedor recebe porque o Follow-up é a fila DELE, e as três coisas que
-- mudaram (onde a tela mora, o que zera um alerta, quantos toques o sistema dá)
-- são a rotina dele. Ligar e desligar continua sendo de dono e gestor.
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('follow-up-na-prospeccao', 'novidade', 'eventos', '{dono,gestor,vendedor}',
 'O Follow-up virou aba da Prospecção — e explica as próprias regras',
 'O Follow-up saiu do menu e virou aba dentro de Prospecção, ao lado do Funil; agora liga e desliga na própria tela, e um bloco novo conta em que ordem o sistema escolhe a próxima ação de cada lead.',
 '/painel/follow-up',
 $txt$O Follow-up morava no menu lateral, longe do Funil e da Régua — e é tudo a mesma coisa: lead. Agora ele é uma aba dentro de Prospecção, a segunda, logo depois do Funil. O endereço antigo continua valendo pra quem tem o atalho salvo.

Junto vieram duas coisas que faltavam.

**Liga e desliga aqui.** Antes, a tela dizia "ligue na Régua do funil" — mandava você pra outra tela pra ligar o que estava olhando. O interruptor agora fica no topo do próprio Follow-up: Desligado, Observando ou Ligado, e grava no clique. Em **Observando** o sistema calcula tudo e anota o que teria feito, sem mandar aviso pra ninguém — é como dá pra ver o quadro antes de soltar os avisos. Quem muda isso é dono ou gestor.

**As regras estão escritas.** No pé da tela, um bloco "Como o Zaq escolhe a próxima ação" conta a ordem que o sistema segue: primeiro quem nunca recebeu resposta nossa, depois quem está esperando você, depois a proposta que precisa de retorno, e só então a escada de toques. Estão lá também os quatro degraus da cobrança (no vencimento, 24h, 48h com o gestor junto, 72h em destaque), os seis estados e as duas travas — os adiamentos seguidos que passam a exigir motivo, e o teto de avisos por dia.

Vale a pena ler uma coisa em especial, porque muda o jeito de trabalhar: **o relógio lê a conversa, não o card**. Mensagem que você mandou pelo próprio celular conta. Abrir o card, arrastar a coluna ou marcar como lido não encerram alerta nenhum — o fato que gerou o aviso continua de pé. O que zera é falar com o cliente, mandar a proposta ou remarcar o prazo.

Nada mudou no motor: os mesmos prazos, a mesma escada, os mesmos avisos.$txt$,
 timestamptz '2026-09-07 18:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'follow-up-na-prospeccao';
