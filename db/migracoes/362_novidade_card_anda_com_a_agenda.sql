-- 362_novidade_card_anda_com_a_agenda.sql
-- O aviso da fase 3a da clínica: o card do funil anda com a agenda
-- (finance/clinica_agenda.card_pela_agenda), seguindo a seção 5 do CLAUDE.md.
--
-- PÚBLICO `clinica`. PRA QUEM: dono, gestor e vendedor (a recepção marca o status).
-- QUEM RECEBE, conferido na produção em 25/09/2026 (só leitura, nicho clinica):
--   39 Espaço Pelle Clínica Dermatologica Ltda (a única conta do nicho)
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('clinica-card-anda-com-a-agenda', 'novidade', 'clinica', '{dono,gestor,vendedor}',
 'O card do paciente anda sozinho com a agenda',
 'Na clínica, o que acontece na agenda move o card do paciente no funil: faltou volta para Follow-up, e ao finalizar a consulta a recepção diz se o médico propôs tratamento.',
 '/painel/clinica/agenda',
 $txt$O funil e a agenda passaram a andar juntos. Ninguém precisa arrastar o card depois da consulta.

MARCOU

Marcar pela agenda leva o card para Consulta agendada (isso já acontecia).

FALTOU OU CANCELOU

O card volta para Follow-up, com a anotação "faltou, remarcar". Não vai para Perdido: faltar não é desistir. Se a falta foi marcada por engano e você reabre o agendamento, o card volta para Consulta agendada.

FINALIZOU

O botão Finalizar agora faz uma pergunta de um clique: o médico propôs tratamento?
- Sim: o card vai para Plano de tratamento, com o valor, se você souber.
- Não: o card vai para Fechado, com o valor da consulta.

O Zaq não lê prontuário, então essa é a única informação que a recepção precisa passar.

O QUE NÃO MUDA

Card que alguém já levou adiante (para Plano de tratamento, Fechado ou Perdido) não volta por causa da agenda. Todo movimento fica no histórico do funil.$txt$,
 timestamptz '2026-09-26 00:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'clinica-card-anda-com-a-agenda';
