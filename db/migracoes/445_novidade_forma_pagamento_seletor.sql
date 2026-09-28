-- 445_novidade_forma_pagamento_seletor.sql
-- O aviso do seletor de forma de pagamento no Plano de pagamento (web/painel_servicos.py),
-- seguindo a seção 5 do CLAUDE.md. Mockup aprovado em
-- docs/mockups/plano_pagamento_forma_selecionavel.html.
--
-- O QUE MUDOU NA TELA. O campo "Forma" de cada parcela (e do gerador "Sinal +
-- parcelas…") era texto livre — cada vendedor escrevia do jeito que lembrava
-- ("cartao", "Cartão de Crédito", "cartão 3x s/ juros" pra descrever a mesma
-- forma). Virou um seletor com as formas comuns (Pix, Cartão de crédito, Cartão
-- de débito, Boleto, Dinheiro, Transferência) mais "Outro…", que abre um campo
-- de texto só quando nada da lista serve. Parcela salva ANTES desta mudança, com
-- qualquer texto em `forma`, continua intacta: se o texto não bate com nenhuma
-- opção, a tela abre em "Outro" com o texto original — regra 0 do CLAUDE.md,
-- informação do cliente não se perde ao carregar a tela.
--
-- O PORTÃO: `eventos`. O card "Plano de pagamento" só existe quando
-- `servico_avulso` (nicho de eventos) — quem vende por setup+mensalidade não
-- tem essa tela (fecha por título único, sem parcela avulsa).
--
-- PRA QUEM: dono e gestor, igual à migração 226 (funil-avisa-fechado-sem-plano):
-- quem monta o plano de pagamento e mexe em contas a receber é quem manda na
-- conta, não o vendedor.
--
-- QUEM RECEBE, conferido na produção em 28/09/2026 (só leitura, nicho eventos):
--   34 Manoel, 35 Louana (as duas contas do nicho)
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('servicos-forma-pagamento-seletor', 'novidade', 'eventos', '{dono,gestor}',
 'A forma de pagamento do plano agora é um seletor',
 'O campo "Forma" de cada parcela do Plano de pagamento deixou de ser texto livre: agora é uma lista (Pix, cartão, boleto, dinheiro, transferência) com "Outro" pra quando nenhuma serve.',
 '/painel/servicos',
 $txt$O Plano de pagamento (Serviços › orçamento de evento) ganhou um seletor no campo "Forma" de cada parcela — antes era texto livre, e a mesma forma de pagamento acabava escrita de jeitos diferentes de uma parcela pra outra.

O QUE MUDA

- Cada parcela (e o gerador "Sinal + parcelas…") mostra uma lista: Pix, Cartão de crédito, Cartão de débito, Boleto, Dinheiro, Transferência.
- Quando nenhuma serve, escolha "Outro…" — abre um campo de texto do lado, do jeito que já funcionava antes.
- Parcela que você já tinha salvo continua exatamente como estava: se o texto não bate com nenhuma opção da lista, ela abre em "Outro" com o seu texto original, sem perder nada.

POR QUE. Forma de pagamento em texto livre não dá pra somar num relatório — "cartao" e "Cartão de Crédito" são duas linhas diferentes mesmo sendo a mesma coisa. Com a lista, esse dado nasce limpo.$txt$,
 timestamptz '2026-09-28 15:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'servicos-forma-pagamento-seletor';
