-- 496_novidade_clinica_receber.sql
-- O aviso da entrega 2a do CRM da clínica (docs/mockups/clinica_crm_telas.html, seção 04,
-- aprovado em 01/10/2026; decisão B do dono em 02/10/2026), seguindo a seção 5 do CLAUDE.md.
--
-- PÚBLICO `clinica`. PRA QUEM: dono, gestor e vendedor (a recepção recebe).
-- QUEM RECEBE, conferido na produção em 02/10/2026 (só leitura, nicho clinica):
--   39 Espaço Pelle Clínica Dermatologica Ltda (a única conta do nicho)
--
-- Sem schema novo (a tabela é a 495). Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('clinica-receber', 'novidade', 'clinica', '{dono,gestor,vendedor}',
 'Receber o atendimento direto na agenda',
 'Cada atendimento do dia mostra se está pago ou a receber, e o botão Receber lança a receita no Financeiro.',
 '/painel/clinica/agenda',
 $txt$A agenda do dia agora cuida do dinheiro do atendimento.

COMO FUNCIONA

- Do Chegou em diante, cada linha da agenda mostra a situação do pagamento: a receber, pago, fica a receber, pacote ou sem custo. O topo do dia conta quantos estão a receber.
- No agendamento, o bloco Pagamento traz o valor do catálogo (dá pra mudar) e a forma: Pix, crédito, débito, dinheiro ou "fica a receber".
- Receber lança a receita no Financeiro, ligada à ficha do paciente. "Fica a receber" vira um título a receber, com vencimento em 30 dias.
- Recebe-se a qualquer hora do dia: na chegada ou na saída.
- Sessão de pacote e sessão inclusa da assinatura não têm Receber: já foram pagas no plano. Atendimento sem preço no catálogo (retorno sem custo, cortesia) aparece como sem custo.

No Financeiro, a receita diz só "Atendimento · nome (categoria)", nunca o procedimento.$txt$,
 timestamptz '2026-10-02 18:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'clinica-receber';
