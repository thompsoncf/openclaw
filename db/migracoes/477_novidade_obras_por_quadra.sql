-- 477_novidade_obras_por_quadra.sql
-- O aviso das obras por quadra, seguindo a seção 5 do CLAUDE.md: PR que muda tela
-- leva o aviso, no mesmo PR. Precisa da 476 e da 350 (o portão `construcao`).
-- Desenho aprovado pelo dono em 01/10/2026: docs/mockups/obras_por_quadra.html.
--
-- PRA QUEM: dono e gestor — quem abre a aba Obras.
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('obras-por-quadra', 'novidade', 'construcao', '{dono,gestor}',
 'As casas por quadra, e a etapa marcada na quadra inteira',
 'Agrupe as casas por quadra (ou setor, bloco — você escolhe o nome). A quadra tem o quadro de etapas de todas as casas, e "terminei a fundação da quadra 5" marca em todas de uma vez.',
 '/painel/obras',
 $txt$Em loteamento a obra anda em lote: a fundação da quadra inteira, depois a alvenaria de todas. Marcar casa por casa, com 20 casas, é trabalho. Agora não precisa.

A QUADRA

Em Obras, "+ Nova quadra" cria o grupo (com o empreendimento, se quiser). Na ficha de cada casa, em "Dados da obra", escolha a quadra e o lote. A lista de obras passa a aparecer por quadra, com o andamento de cada uma. Não chama quadra aí? Mude o nome do grupo pra Setor, Bloco, o que for.

O QUADRO DE ETAPAS

Cada quadra tem o quadro: as casas nas linhas, as etapas nas colunas — o que está feito, o que está feito e pago ao empreiteiro, o que foi pago antes de ficar pronto. Escolha as casas e marque a etapa em todas de uma vez.

PELO WHATSAPP E PELO TELEGRAM

"Terminei a fundação da quadra 5": o assistente marca nas casas que já começaram e diz quais ficaram de fora. Errou? "Desfaz". A nota "pra quadra 5" é dividida entre as casas dela pelo m², e o pagamento do empreiteiro da quadra inteira marca as etapas pagas em cada casa.$txt$,
 timestamptz '2026-10-01 22:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'obras-por-quadra';
