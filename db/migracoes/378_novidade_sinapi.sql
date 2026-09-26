-- 378_novidade_sinapi.sql
-- O aviso da referência SINAPI na ficha da obra, seguindo a seção 5 do CLAUDE.md:
-- PR que muda tela leva o aviso, no mesmo PR. Precisa da 377 e da 350 (o portão
-- `construcao`). Desenho aprovado pelo dono em 25/09/2026:
-- docs/mockups/nicho_construcao.html, seção 06.
--
-- QUEM RECEBE, conferido na produção em 26/09/2026 (contas × nichos, só leitura):
--   conta 33 · Pablo Thyago G. Dias / PX2 Empreendimentos · Lago da Pedra-MA
--
-- PRA QUEM: dono e gestor.
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('obras-referencia-sinapi', 'novidade', 'construcao', '{dono,gestor}',
 'O custo do m² da obra ao lado da referência do IBGE',
 'A ficha da obra mostra o custo médio do m² de construção no seu estado (SINAPI, do IBGE) ao lado do custo por m² da sua obra.',
 '/painel/obras',
 $txt$"R$ 1.700 o m² está bom?" Sem referência, ninguém sabe. Agora a ficha da obra mostra.

A REFERÊNCIA

O SINAPI é o custo médio do m² de construção que o IBGE publica todo mês, por estado. No Maranhão, em agosto de 2026, foi R$ 1.969,07 — R$ 1.197,36 de material e R$ 771,71 de mão de obra. O Zaq busca o número novo sozinho, todo mês.

A COMPARAÇÃO

Com a área da obra cadastrada, a ficha mostra quanto a sua obra está acima ou abaixo da referência. Obra pronta compara o que foi gasto de verdade; obra em andamento compara o custo previsto — comparar o gasto de meia obra com o custo de uma obra inteira diria que está barato só porque não acabou.

É PRA COMPARAR, NÃO PRA COBRAR

O SINAPI é um padrão residencial médio. Casa popular bem tocada sai abaixo dele — e é esse o ponto.$txt$,
 timestamptz '2026-09-26 23:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'obras-referencia-sinapi';
