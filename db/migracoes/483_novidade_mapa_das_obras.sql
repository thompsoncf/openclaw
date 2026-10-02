-- 483_novidade_mapa_das_obras.sql
-- O aviso do mapa das obras (web/painel_obras_mapa.py), seguindo a seção 5 do
-- CLAUDE.md. Desenho aprovado pelo dono em 02/10/2026. Precisa da 350 (o portão
-- `construcao`).
--
-- PRA QUEM: dono e gestor — quem acompanha as obras pelo painel.
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('obras-mapa-3d', 'novidade', 'construcao', '{dono,gestor}',
 'O mapa das suas obras, na sua planta',
 'Suba a planta do loteamento, risque os lotes por cima e veja o empreendimento inteiro num mapa 3D: cada casa cresce no mapa conforme as etapas avançam, e um toque abre o perfil com a casa desenhada camada por camada.',
 '/painel/obras/mapa',
 $txt$Quem constrói em loteamento enxerga a obra pelo mapa, não por lista. Agora o painel tem o seu.

A SUA PLANTA, DE VERDADE

Em Obras › Mapa das obras, crie a área (o empreendimento — pode ter várias) e suba a planta: serve o PDF do loteamento ou uma foto. Ela vira o fundo do mapa.

RISCAR OS LOTES

Por cima da planta, risque cada lote com o dedo ou o mouse. Tem a ferramenta de fileira: risca a fileira inteira de uma vez, diz quantos lotes são, e ela divide igualzinho. Lote que não é seu, marque "de terceiro"; lote vazio fica como "vago". Ligue cada lote à casa dele e pronto.

O MAPA QUE CRESCE

Na vista 3D, cada lote sobe do chão conforme a obra avança — dá pra ver de longe o que está pronto (ganha telhado no mapa), o que está parado e o que tem alerta. Um toque no lote abre o perfil: a casa desenhada camada por camada (fundação, estrutura, paredes, telhado, pintura), as etapas, o gasto contra o previsto e o atalho pra ficha completa.$txt$,
 timestamptz '2026-10-02 17:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'obras-mapa-3d';
