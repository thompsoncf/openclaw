-- 387_novidade_produtos_da_clinica.sql
-- O aviso do produto junto do atendimento (finance/clinica_produtos.py, /painel/clinica/produtos),
-- seguindo a seção 5 do CLAUDE.md.
--
-- PÚBLICO `clinica`. PRA QUEM: dono, gestor e vendedor (a recepção vende no fim do
-- atendimento; a duração do produto é do dono).
-- QUEM RECEBE, conferido na produção em 26/09/2026 (só leitura, nicho clinica):
--   39 Espaço Pelle Clínica Dermatologica Ltda (a única conta do nicho)
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('clinica-produtos', 'novidade', 'clinica', '{dono,gestor,vendedor}',
 'Produto junto do atendimento: venda no fim da consulta e reposição lembrada sozinha',
 'Venda o dermocosmético na tela do agendamento; com a duração do produto cadastrada, o Zaq pergunta ao paciente se acabou na data certa, e o que vence logo na prateleira vira sugestão de venda.',
 '/painel/clinica/produtos',
 $txt$O dermocosmético que já está na prateleira agora é vendido junto do atendimento.

NO FIM DO ATENDIMENTO

- A tela do agendamento tem a caixa Produto: escolha, a quantidade e a forma de pagamento. O estoque baixa e a receita entra em Vendas no Financeiro.
- Ela sugere a reposição do que o paciente já leva e o que está vencendo na prateleira.
- Assinante leva o desconto do plano sozinho.

A REPOSIÇÃO VOLTA SOZINHA

- Cadastre quanto cada produto dura (ex.: protetor, 60 dias). Na data prevista, o Zaq pergunta ao paciente se acabou e oferece outro.
- A mensagem nunca diz o nome do produto, sai só no horário de atendimento e conta no limite de 1 mensagem automática por paciente por dia.

VALIDADE

- A entrada de estoque pela tela da clínica guarda a validade do lote. O que vence em até 60 dias aparece como sugestão de venda, antes de virar perda.

Fica em Agenda › Pacotes › Produtos. O cadastro completo (preço, foto, perda) continua em Produtos.$txt$,
 timestamptz '2026-09-27 00:10:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'clinica-produtos';
