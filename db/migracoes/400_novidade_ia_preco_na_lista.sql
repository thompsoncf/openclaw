-- 400_novidade_ia_preco_na_lista.sql
-- O aviso da chave "A IA pode dizer este preço" direto na LISTA de serviços (mockup
-- docs/mockups/servicos_ia_preco_na_lista.html, aprovado pelo dono em 27/09/2026),
-- seguindo a seção 5 do CLAUDE.md.
--
-- PÚBLICO `mais_de_um_chip` (migração 389): a chave só existe onde existe a IA da regra
-- por número — o mesmo portão da tela (`ia_preco` em web/painel_servicos.py).
-- PRA QUEM: dono e gestor (quem cuida do catálogo e da IA).
-- QUEM RECEBE, conferido na produção em 27/09/2026 (só leitura): 34 Prime Eventos
-- (e toda empresa que tiver dois chips).
--
-- Aditiva e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('ia-preco-na-lista', 'novidade', 'mais_de_um_chip', '{dono,gestor}',
 'Liberar o preço pra IA direto na lista de serviços',
 'Cada serviço ganhou um botão "IA diz o preço" na lista, que salva no toque, com um contador e o "Liberar todos" — sem abrir serviço por serviço.',
 '/painel/servicos',
 $txt$Liberar o preço pra IA agora é na lista, não serviço por serviço.

- Em Serviços, abra "ver os serviços em ordem alfabética": cada linha tem o botão lilás "IA diz o preço" / "IA não diz". Um toque liga ou desliga e já salva.
- No topo, o contador mostra quantos estão liberados, e dá pra "Liberar todos" ou "Nenhum" de uma vez (com confirmação).
- A IA do número só diz o valor dos liberados, sempre como "a partir de". Os outros ela não cita: convida pra visita e o orçamento sai conferido.
- No formulário de editar, a chave continua lá, agora com o texto colado nela.$txt$,
 timestamptz '2026-09-27 12:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'ia-preco-na-lista';
