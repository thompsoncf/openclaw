-- 339_novidade_funil_enxuto.sql
-- O aviso do funil enxuto, parte 1 (o cartão e as colunas), seguindo a seção 5 do
-- CLAUDE.md. Pedido do dono em 24/09/2026 ("veja algo mais profissional"), mockup
-- aprovado em docs/mockups/prospeccao_layout.html com as decisões: tela inteira,
-- excluir só dono e gestor, período padrão = mês atual.
--
-- PÚBLICO: `servico` — o funil é de quem vende serviço (eventos, recorrente,
-- seguros, clínica); produto não tem funil. O texto não fala de festa.
-- PRA QUEM: dono, gestor e vendedor — muda a rotina de todo mundo que usa o
-- quadro (e o vendedor precisa saber que excluir saiu da mão dele).
--
-- Aditiva e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('funil-enxuto', 'novidade', 'servico', '{dono,gestor,vendedor}',
 'O funil ficou mais enxuto: mais leads por tela',
 'No funil, cada lead virou um cartão de quatro linhas, as colunas ficaram mais largas e rolam por dentro, e as ações foram pro botão ⋯.',
 '/painel/prospeccao',
 $txt$O funil foi redesenhado pra caber mais leads na tela sem perder nada.

O CARTÃO TEM QUATRO LINHAS

Quem é, o que quer, o que foi dito por último e qual é o próximo passo. Cabem 6 ou 7 cartões por coluna, onde antes cabiam 2 ou 3. O nome não corta mais com 13 letras.

- Clique na última mensagem pra abrir a conversa.
- O botão ⋯ do cartão tem mover para outra etapa (funciona no celular), trocar o responsável, abrir a ficha e excluir.
- A bolinha com as iniciais é o responsável: dono e gestor trocam clicando nela.
- O próximo passo é texto: "Crítico · 5d", "Venceu há 18h", "Agendado 25/09".
- O filete verde na esquerda quer dizer que o cliente está esperando resposta.

AS COLUNAS

Ficaram mais largas e usam a tela inteira. Cada uma rola por dentro, com o nome da etapa sempre à vista, e embaixo do nome aparece quantos estão esperando resposta e quantos estão parados. Coluna vazia vira uma faixa fina: clique nela pra abrir.

Cartão que muda de etapa entra no topo da coluna nova.

EXCLUIR LEAD AGORA É SÓ DO DONO E DO GESTOR

Excluir apaga o lead de vez. O vendedor move o lead pra Perdido quando não der certo.$txt$,
 timestamptz '2026-09-24 22:30:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'funil-enxuto';
