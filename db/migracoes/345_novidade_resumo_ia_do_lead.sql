-- 345_novidade_resumo_ia_do_lead.sql
-- O aviso do resumo da conversa e da sugestão da IA no card do lead (migração 344,
-- finance/resumo_ia.py).
--
-- QUEM RECEBE (CLAUDE.md §5 e §6). Público `servico`: o ⋯ do card é do funil, que
-- toda conta que vende serviço tem — eventos, consultoria, clínica, corretora.
-- Vai pro VENDEDOR também: é a rotina dele que muda (o ✨ na conversa do app). O
-- texto não fala de festa nem de reunião — o que a IA escreve já sai no
-- vocabulário de cada conta; o aviso só diz onde está o botão.
--
-- Contas alcançadas em 25/09/2026 (servico): 3, 16, 21, 23, 30, 33, 34, 35, 37, 39
-- — a lista com nome vai no corpo do PR.
--
-- Aditiva e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('resumo-ia-no-card-do-lead', 'novidade', 'servico', '{dono,gestor,vendedor}',
 'Resumo da conversa e sugestão da IA no card do lead',
 'Um toque no card do lead e a IA resume a conversa, diz o próximo passo e deixa uma mensagem pronta pro vendedor revisar e mandar.',
 '/painel/prospeccao',
 $txt$A IA agora lê a conversa do lead por você.

ONDE ESTÁ

No funil, toque no ⋯ do card e escolha "✨ Resumo e sugestão da IA". Na janela do lead, é o botão "✨ Resumo IA", ao lado de Ligar e WhatsApp. No app do vendedor, é o "✨ IA" no alto da conversa.

O QUE ELA MOSTRA

O que o cliente quer, em que pé está a negociação, o que pode travar a venda e qual o próximo passo. Em cima, em verde, o que o sistema sabe de certeza: com quem está a bola e há quanto tempo, e quantas mensagens foram lidas.

E uma mensagem pronta, que dá pra editar. "Usar na conversa" coloca o texto no campo da conversa. Quem envia é você: nada sai sozinho.

O QUE ELA NÃO FAZ

Não inventa preço, desconto, parcela nem horário livre. Quando o cliente pergunta algo que não está na conversa nem no orçamento, aparece uma caixa pedindo pra confirmar antes de prometer.

O resumo fica guardado até chegar mensagem nova: abrir de novo é na hora. Os botões 👍 e 👎 dizem se ajudou.$txt$,
 timestamptz '2026-09-25 12:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'resumo-ia-no-card-do-lead';
