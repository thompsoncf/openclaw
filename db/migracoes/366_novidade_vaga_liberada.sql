-- 366_novidade_vaga_liberada.sql
-- O aviso da vaga liberada da clínica (finance/clinica_vagas.py, 365), seguindo a
-- seção 5 do CLAUDE.md.
--
-- PÚBLICO `clinica`. PRA QUEM: dono, gestor e vendedor (a recepção aprova o convite).
-- QUEM RECEBE, conferido na produção em 26/09/2026 (só leitura, nicho clinica):
--   39 Espaço Pelle Clínica Dermatologica Ltda (a única conta do nicho)
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('clinica-vaga-liberada', 'novidade', 'clinica', '{dono,gestor,vendedor}',
 'Consulta cancelada vira convite para quem cabe no horário',
 'Quando alguém cancela com pelo menos 1 hora de aviso, o Zaq mostra quem cabe no horário e manda o convite pelo WhatsApp depois que a recepção aprova. Fica com quem responder 1 primeiro.',
 '/painel/clinica/vagas',
 $txt$O horário que abre por cancelamento agora vai para quem cabe nele, sem a recepção ligar um por um.

COMO FUNCIONA

Quando uma consulta é cancelada com pelo menos 1 hora de aviso, o horário aparece em Agenda › ⚡ Vagas liberadas, com a lista de quem cabe nele, em ordem:
1. quem pediu horário pelo WhatsApp e ainda não marcou;
2. quem já vem à clínica nesse dia, com outro profissional;
3. quem recebeu o preço da consulta e não marcou;
4. quem está com o retorno vencido.

A recepção aperta "Aprovar e mandar". O convite vai para 3 pessoas ao mesmo tempo, vale 20 minutos e fica com quem responder 1 primeiro: a consulta entra na agenda na hora ("veio de vaga liberada") e o card vai para Consulta agendada. Quem responde depois recebe "acabou de ser preenchida". Sem resposta em 20 minutos, o convite vai sozinho para mais 5.

AS REGRAS

- A mensagem diz profissional, dia, hora e lugar. Nunca o procedimento.
- Só no horário de atendimento da Régua, e nunca com menos de 1 hora de aviso (aí a tela diz "ofereça no balcão").
- No máximo 1 mensagem automática por paciente por dia, somando o voltar a chamar. O convite ocupa o toque do dia.
- Todo convite ensina a responder PARAR, e quem responde nunca mais recebe aviso de vaga.
- Quem já tem horário nesse dia, quem já marcou consulta e quem pediu para sair não é chamado.

O MODO

Começa em "a recepção aprova cada horário", porque é mensagem que o paciente não pediu. Depois do primeiro mês, o dono pode passar para "automático" no fim da tela de vagas.$txt$,
 timestamptz '2026-09-26 18:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'clinica-vaga-liberada';
