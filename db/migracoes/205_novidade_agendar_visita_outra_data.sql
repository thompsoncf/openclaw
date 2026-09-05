-- 205_novidade_agendar_visita_outra_data.sql
-- Aviso da pílula "Outra data" na tela Agendar visita do Cockpit (o vendedor
-- reclamou em 05/09/2026, com print em mãos: os 14 dias fixos não alcançam a
-- visita que o cliente pede pra três semanas depois). É de qualquer conta, sem
-- nicho — por isso 'todos', igual à 'fila-no-mes-atual' (200), o analogo mais
-- próximo: mudança só na rotina do vendedor, no app dele.
--
-- QUEM RECEBE: todo vendedor que usa o Cockpit — a tela é a mesma pra qualquer
-- conta, sem gate de nicho.
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('agendar-visita-outra-data', 'novidade', 'todos', '{vendedor}',
 'Agendar visita ganhou a pílula "Outra data"',
 'Na tela de agendar visita, uma pílula nova abre um calendário sem limite de dias — pra quando o cliente pede uma data mais distante.',
 '/cockpit',
 $txt$Antes, o Dia só oferecia os próximos 14 — hoje e mais 13. Se o cliente pedia uma visita pra daqui a três semanas, não tinha pílula, campo ou jeito de marcar.

Agora, depois dos 14 dias de sempre, tem uma pílula a mais: "📅 Outra data". Ela abre um calendário de verdade, sem limite nenhum, e some assim que você escolhe a data — a pílula passa a mostrar o dia escolhido, e o resto da tela continua igual.$txt$,
 timestamptz '2026-09-05 12:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'agendar-visita-outra-data';
