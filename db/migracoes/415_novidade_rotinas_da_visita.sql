-- 415_novidade_rotinas_da_visita.sql
-- O aviso da migração 414 (as rotinas da visita, funil novo de eventos parte 2a —
-- docs/mockups/funil_novo_rotinas.html), seguindo a seção 5 do CLAUDE.md.
--
-- PÚBLICO `eventos` (§6): a visita ao espaço é de quem vende festa.
-- PRA QUEM: dono, gestor e vendedor — o vendedor passa a receber o "veio?" e o
-- lembrete de falar com o cliente, e o cliente dele passa a receber a confirmação.
-- QUEM RECEBE: toda conta que vende festa. Na Prime (34) as rotinas já vêm ligadas
-- (migração 414, com a autorização do dono); nas outras, nascem desligadas, e o corpo
-- diz onde ligar.
--
-- Aditiva e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('rotinas-da-visita', 'novidade', 'eventos', '{dono,gestor,vendedor}',
 'A visita ao espaço agora é confirmada e acompanhada',
 'Quem vende festa passa a ter a visita ao espaço confirmada com o cliente na véspera e 2h antes, e a equipe é lembrada de perguntar se ele veio e de falar com ele depois.',
 '/painel/prospeccao',
 $txt$A visita ao espaço ganhou rotinas, pros dois jeitos de atender (vendedor e IA):

- CONFIRMAÇÃO: a visita que a equipe marca recebe as mesmas mensagens que a IA já mandava: ao marcar, na véspera às 18h ("responda 1 para confirmar ou 2 para remarcar") e 2h antes, pelo número da conversa. Se o cliente pedir pra remarcar, quem atende é avisado. Se já confirmou pelo seu celular, toque em "Cliente já confirmou" na agenda do app e as mensagens param.
- VEIO?: 1h depois do horário, quem recebeu a visita é perguntado se o cliente veio (e de novo às 18h). A resposta é o botão que já existe na agenda do app, e ela move o card.
- DEPOIS DA VISITA: veio e ninguém escreveu em 2h, o vendedor é lembrado de mandar a proposta. Na visita que a IA marcou, a IA agradece e oferece o orçamento.

No card do funil aparece o estado da visita: confirmada, pediu pra remarcar, não confirmou, falar com o cliente.

Cada rotina se liga e desliga em Funil › Régua › Rotinas da visita.$txt$,
 timestamptz '2026-09-28 11:30:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'rotinas-da-visita';
