-- 475_novidade_clinica_plano_pago.sql
-- O aviso da entrega 1b do CRM da clínica (docs/mockups/clinica_crm_telas.html, seção 01,
-- "O dinheiro", aprovado em 01/10/2026), seguindo a seção 5 do CLAUDE.md.
--
-- PÚBLICO `clinica`. PRA QUEM: dono, gestor e vendedor (a recepção é quem clica Recebi
-- e "Paciente não quis").
-- QUEM RECEBE, conferido na produção em 01/10/2026 (só leitura, nicho clinica):
--   39 Espaço Pelle Clínica Dermatologica Ltda (a única conta do nicho)
--
-- Sem schema novo. Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('clinica-plano-pago', 'novidade', 'clinica', '{dono,gestor,vendedor}',
 'Plano aceito espera o pagamento; plano que não fecha não perde o paciente',
 'O paciente só vai para Em tratamento quando o pagamento ou a entrada entram. E o plano recusado ou vencido leva o cartão para Retorno ou Concluído, com o motivo.',
 '/painel/clinica/planos',
 $txt$Vale para quem já aplicou o funil novo da clínica (Funil › Régua › "As etapas do funil").

PLANO ACEITO NÃO É PLANO PAGO

- O paciente aceitou (pelo link, respondendo 1, 2 ou 3, ou na recepção): o cartão fica em "Plano ou orçamento enviado", aceito e aguardando o pagamento.
- Quando o pagamento ou a entrada entrar, abra o plano e clique em "Recebi o pagamento". A primeira parcela recebe a baixa e o cartão vai para Em tratamento.
- Se a baixa for feita pelo financeiro, o Zaq percebe sozinho e leva o cartão do mesmo jeito.
- A tela Hoje mostra quem aceitou e ainda não pagou.

PLANO QUE NÃO FECHA

- Plano recusado pelo link, vencido sem resposta, ou "Paciente não quis" na recepção (com o motivo: achou caro, vai pensar, fez em outra clínica...): o cartão vai para Retorno, se há retorno a fazer, ou para Concluído. O valor do cartão volta a ser o da consulta.
- O paciente que saiu da consulta sem querer o plano: na tela de montar o plano, use "O paciente não quis o plano".
- Quem nunca foi atendido (orçamento direto pelo WhatsApp) continua com a vendedora, na coluna do plano.
- "Cancelar plano" continua existindo, para refazer o plano: ele não mexe no cartão.$txt$,
 timestamptz '2026-10-02 12:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'clinica-plano-pago';
