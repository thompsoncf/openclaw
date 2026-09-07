-- 219_novidade_follow_up.sql
-- O aviso do follow-up automático (CLAUDE.md §5: PR que muda tela leva o aviso).
--
-- PÚBLICO 'eventos' e não 'todos' (§6): o Follow-up nasce só no perfil de
-- eventos, porque o segundo relógio da régua é a data da festa. Quem vende
-- mensalidade tem a mesma escada sem esse relógio, e entra na rodada seguinte —
-- não faz sentido avisar de uma tela que a conta ainda não tem
-- (finance/follow_up.PERFIS_COM_TELA).
--
-- PRA QUEM: dono, gestor e vendedor. É o único dos três avisos recentes que o
-- vendedor recebe de fato — muda a rotina dele todo dia, e é ele quem passa a
-- receber push quando um follow-up vence.
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('follow-up-automatico', 'novidade', 'eventos', '{dono,gestor,vendedor}',
 'Follow-up automático: o sistema marca a próxima ação e cobra quando ela vence',
 'Todo lead em jogo passa a ter próxima ação e prazo, propostos pelo sistema. Quando o prazo vence sem ninguém falar com o cliente, o vendedor é avisado — e o gestor entra depois de 48h.',
 '/painel/follow-up',
 $txt$Ninguém precisa mais preencher "próximo contato" pra saber quem está esperando.

Cada lead em jogo ganhou uma próxima ação e um prazo, propostos pelo Zaq: responder quem está esperando resposta, cobrar a proposta parada, dar o segundo toque em quem não respondeu. Quando a festa está a menos de trinta dias e ainda não saiu proposta, o prazo é hoje, por mais recente que tenha sido a última conversa.

A tela nova é o Follow-up, no menu. No topo, quatro números: quantos vencem hoje, quantos estão atrasados, quantos estão críticos há mais de 72h e quantos o sistema não tem o que propor. Cada número abre a lista, e a lista mostra o que importa em cada lead: o que fazer, quando venceu, de quem é a bola, há quanto tempo ninguém fala com ele e quantas tentativas já foram feitas sem resposta.

O relógio lê a conversa, não o card. Mensagem que o vendedor manda pelo próprio celular conta igual. E abrir o card não encerra nada: o aviso só para quando existe ação de verdade — mensagem enviada, proposta, compromisso marcado ou um novo prazo com motivo.

Quando o prazo vence, o vendedor recebe o aviso; 24h depois, de novo; a partir de 48h o gestor entra junto. Os avisos saem agrupados e respeitam o horário de atendimento — ninguém é acordado de madrugada nem no domingo.

Remarcar é permitido e fica registrado: quem adiou, pra quando e por quê. Do terceiro adiamento seguido sem nenhuma mensagem no meio, o motivo passa a ser obrigatório e o lead aparece marcado no painel de quem gerencia.

Os avisos nascem desligados. A tela já mostra o quadro; o push e o e-mail só começam quando o dono ligar.$txt$,
 timestamptz '2026-09-07 12:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'follow-up-automatico';
