-- 581_novidade_clinica_passagem_e_encaixe.sql
-- O aviso da entrega 2c do CRM da clínica (docs/mockups/clinica_crm_telas.html, seções 04
-- e 05, aprovado em 01/10/2026; decisão C do dono em 02/10/2026), seguindo a seção 5 do
-- CLAUDE.md. Número 581 com folga (regra de numeração: o maior em uso era 551).
--
-- PÚBLICO `clinica`. PRA QUEM: dono, gestor e vendedor (é a recepção que cuida da agenda).
-- QUEM RECEBE, conferido na produção em 02/10/2026 (só leitura, nicho clinica):
--   39 Espaço Pelle Clínica Dermatologica Ltda (a única conta do nicho)
--
-- Sem schema novo (a coluna é a 580). Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('clinica-passagem-e-encaixe', 'novidade', 'clinica', '{dono,gestor,vendedor}',
 'Encaixe de quem chegou, cancelar uma passagem e finalizar com um toque',
 'Quem chega sem marcar entra na hora; a clínica pode cancelar a ida a uma cidade sem contar falta pra ninguém; e vacina, coleta e sessão se finalizam com um toque.',
 '/painel/clinica/agenda',
 $txt$Mais quatro coisas da agenda do dia.

O QUE MUDA

- + Encaixe (chegou sem marcar): no dia de hoje, informe o profissional, o atendimento e o paciente. Ele entra na hora, como encaixe, já com Presente (mesmo com os encaixes do dia esgotados: quem está na recepção entra).
- Cancelar a passagem: no topo de cada profissional, na agenda do dia (um botão por lugar, quando o dia tem mais de um). A clínica cancela a ida àquele lugar: o horário de lá fica bloqueado (a sede da manhã segue aberta se só a cidade da tarde foi cancelada), os marcados ficam "a remarcar" com o selo "a clínica desmarcou" (não conta como falta), o retorno sem custo deles vale até a próxima passagem pela cidade, e cada um ganha uma mensagem pronta com a próxima data (ou a sede) pra recepção mandar.
- Finalizar com um toque: sessão, vacina, coleta, exame e procedimento não perguntam mais do tratamento. A pergunta é da consulta e da avaliação.
- Saiu sem ser atendido: no Finalizar. O horário fica livre, não conta como falta, e o cartão volta para Follow-up, pra remarcar.$txt$,
 timestamptz '2026-10-03 12:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'clinica-passagem-e-encaixe';
