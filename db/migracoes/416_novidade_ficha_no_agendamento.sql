-- 416_novidade_ficha_no_agendamento.sql
-- O aviso do passo 0c-2a (a ficha no agendamento e a mensagem pra quem recebe, migração
-- 415), seguindo a seção 5 do CLAUDE.md.
--
-- PÚBLICO `clinica`. PRA QUEM: dono, gestor e vendedor (a recepção agenda).
-- QUEM RECEBE, conferido na produção em 27/09/2026 (só leitura, nicho clinica):
--   39 Espaço Pelle Clínica Dermatologica Ltda (a única conta do nicho)
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('clinica-ficha-no-agendamento', 'novidade', 'clinica', '{dono,gestor,vendedor}',
 'A mãe que marca pro filho: a ficha é dele, a mensagem é pra ela',
 'O agente e a recepção guardam a data de nascimento ao marcar; a consulta do filho vai pra ficha dele, com a mãe como responsável, e a confirmação diz "Lúcia, a consulta de Pedro".',
 '/painel/clinica/agenda',
 $txt$O agendamento agora começa a ficha do jeito certo.

NO WHATSAPP (o agente)

- Ao oferecer os horários, o agente pede o nome completo e a data de nascimento de quem vai ser atendido, e se é pra própria pessoa ou pra outra. A data é opcional: sem ela, marca assim mesmo.
- "É pro meu filho" sem o nome: ele pergunta o nome antes de marcar.
- Nunca pergunta sintoma, alergia ou remédio: isso é do formulário do link, que só o profissional lê.

NO BALCÃO (a recepção)

- O agendamento ganhou a data de nascimento e a caixa "a consulta é de outra pessoa deste contato (ex.: o filho)".

A FICHA E A MENSAGEM

- A data vai pra ficha do paciente. Menor de idade marcado por outra pessoa ganha ela como responsável.
- A confirmação e a véspera falam com quem recebe: "Lúcia, a consulta de Pedro…" e "Oi, Lúcia! Amanhã Pedro tem consulta…".
- A mãe que marcou no nome dela e depois disse "era pro meu filho": a consulta passa pra ficha dele.$txt$,
 timestamptz '2026-09-27 18:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'clinica-ficha-no-agendamento';
