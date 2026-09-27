-- 406_novidade_funil_sem_piscar.sql
-- O aviso do funil que se atualiza sem recarregar a página (docs/mockups/
-- funil_atualizacao.html, aprovado pelo dono em 27/09/2026 "com as recomendações"),
-- seguindo a seção 5 do CLAUDE.md.
--
-- PÚBLICO `todos`: o funil é o mesmo quadro em toda conta, e a troca vale pra todas.
-- PRA QUEM: dono, gestor e vendedor — é a tela onde o vendedor passa o dia, e o que
-- ele vê muda (o selo no título, o contorno no card novo, o fim da tela desmontada).
-- QUEM RECEBE: toda conta com funil.
--
-- Aditiva e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('funil-sem-piscar', 'mudanca', 'todos', '{dono,gestor,vendedor}',
 'O funil se atualiza sem piscar',
 'O funil continua se atualizando sozinho a cada minuto, mas agora sem recarregar a página: os cards trocam no lugar e a tela não se desmonta mais.',
 '/painel/prospeccao',
 $txt$O funil se atualiza sozinho a cada minuto — é assim que lead novo e mensagem nova aparecem na tela. Até hoje ele recarregava a página inteira, e por um instante a tela aparecia desmontada e pulava.

Agora:
- Só os dados trocam, no lugar. A rolagem de cada coluna, as dobras abertas e a trilha escolhida ficam como estavam.
- Ao lado do título, um selo diz "atualizado 10:42", "atualizando…" ou "pausado · você está mexendo". Um toque nele atualiza na hora.
- O card que entrou ou mudou de coluna ganha um contorno verde por alguns segundos.
- Com a ficha de um lead aberta, a busca digitada, um card sendo arrastado ou um menu aberto, a atualização espera.$txt$,
 timestamptz '2026-09-27 21:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'funil-sem-piscar';
