-- 361_novidade_agenda_clinica.sql
-- O aviso da agenda da clínica (web/painel_clinica_agenda.py, migração 360),
-- seguindo a seção 5 do CLAUDE.md.
--
-- PÚBLICO `clinica`. PRA QUEM: dono, gestor e vendedor — a recepção entra como
-- vendedor e é quem marca.
-- QUEM RECEBE, conferido na produção em 25/09/2026 (só leitura, nicho clinica):
--   39 Espaço Pelle Clínica Dermatologica Ltda (a única conta do nicho)
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('clinica-agenda', 'novidade', 'clinica', '{dono,gestor,vendedor}',
 'Agenda da clínica: uma coluna por profissional e confirmação na véspera',
 'Clínicas ganharam a agenda do dia, com uma coluna por profissional e a semana de cada um, agendamento em poucos cliques, os status do atendimento e a confirmação pelo WhatsApp na véspera.',
 '/painel/clinica/agenda',
 $txt$O item Agenda do menu agora abre a agenda da clínica.

O DIA E A SEMANA

No Dia, cada profissional é uma coluna, só com os horários em que ele atende. Na Semana, você escolhe um profissional e vê os dias lado a lado, com a ocupação de cada um. No topo aparecem a ocupação, quantos já confirmaram e as faltas.

AGENDAR

Clique num horário livre (ou em + Agendar). Escolha o atendimento e o horário, busque o paciente pelo nome ou telefone, ou cadastre na hora só com nome e celular. O fim do horário, o local e o valor se calculam. Horário ocupado pode entrar como encaixe, até o limite de encaixes do dia. Marcar leva o card do paciente para Consulta agendada no funil.

"Agendar e mandar confirmação" já manda para o paciente: "Prontinho!! Sua consulta com o Dr. Manoel está marcada para…". A mensagem nunca diz o procedimento.

OS STATUS

Agendado, Confirmado, Chegou, Em atendimento e Finalizado, ou Faltou e Cancelado. Cancelado e falta liberam o horário. O agendamento também pode ser remarcado para outro horário livre.

CONFIRMAÇÃO NA VÉSPERA (começa desligada)

O dono ou o gestor liga no fim da página da Agenda. Na véspera, a partir da hora escolhida, quem tem horário recebe: "Amanhã você tem consulta às 09:00… Responda 1 para confirmar ou 2 se precisar remarcar." Quem responde 1 fica Confirmado sozinho. Quem responde 2 aparece em destaque no topo da Agenda para a recepção remarcar.$txt$,
 timestamptz '2026-09-25 23:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'clinica-agenda';
