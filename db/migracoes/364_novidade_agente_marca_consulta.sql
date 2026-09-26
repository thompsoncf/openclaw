-- 364_novidade_agente_marca_consulta.sql
-- O aviso da fase 3b da clínica: o agente do WhatsApp marca consulta e passa pra
-- recepção o que não é dele (finance/clinica_agente.py), seguindo a seção 5 do
-- CLAUDE.md.
--
-- PÚBLICO `clinica`. PRA QUEM: dono, gestor e vendedor (a recepção entra como
-- vendedor e é quem recebe o "O agente passou pra você").
-- QUEM RECEBE, conferido na produção em 26/09/2026 (só leitura, nicho clinica):
--   39 Espaço Pelle Clínica Dermatologica Ltda (a única conta do nicho)
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('clinica-agente-marca-consulta', 'novidade', 'clinica', '{dono,gestor,vendedor}',
 'O agente do WhatsApp marca consulta',
 'Com o agente ligado, ele diz o preço, oferece horários livres da agenda e marca quando o paciente escolhe. O que não é dele (saúde, foto, áudio, convênio, desconto) ele passa pra recepção, na tela Hoje.',
 '/painel/hoje',
 $txt$O agente de atendimento do WhatsApp agora trabalha como a recepção da clínica.

O QUE ELE FAZ

- Diz o preço do atendimento, só nos que estão com "o agente pode dizer este preço" marcado em Configurar › Atendimentos. Nos outros, diz que a recepção informa.
- Oferece até 3 horários livres de verdade, lidos da grade de cada profissional, e marca quando o paciente escolhe. A consulta entra na agenda com "marcado pelo agente no WhatsApp", o card vai para Consulta agendada e você recebe um aviso.
- Só marca os atendimentos com "o agente pode marcar sozinho" marcado. Retorno e sessão, só para quem já foi atendido aqui.
- Responde o "1" e o "2" do lembrete da véspera.

O QUE ELE NUNCA FAZ

Não fala de sintoma, não indica tratamento, não interpreta foto nem exame, não diz se o convênio cobre e não dá desconto. Nesses casos ele responde com um texto fixo da clínica, avisa a recepção e o paciente aparece no topo da tela Hoje, em "O agente passou pra você". Foto, áudio e documento vão para lá também, porque ele não vê nem ouve.

Urgência (sangramento, falta de ar, reação alérgica) recebe na hora a orientação de procurar o pronto-socorro ou ligar 192, e sobe em vermelho na tela Hoje.

O item sai da lista quando alguém responde a conversa, ou no botão Resolvido.

COMO LIGAR

O agente se configura em Comunicação › 🤖 Agente IA. Para testar antes de soltar para todos, deixe o agente desligado ali e use "🤖 Ativar agente" numa conversa só, a sua.

Antes, confira em Configurar a grade de horário de cada profissional: sem grade, o agente não tem horário para oferecer e passa o pedido de marcação para a recepção.$txt$,
 timestamptz '2026-09-26 12:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'clinica-agente-marca-consulta';
