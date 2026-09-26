-- 370_novidade_fotos_da_obra.sql
-- O aviso das fotos da obra, seguindo a seção 5 do CLAUDE.md: PR que muda tela
-- leva o aviso, no mesmo PR. Precisa da 369 (as fotos) e da 350 (o portão
-- `construcao`). Desenho aprovado pelo dono em 25/09/2026:
-- docs/mockups/nicho_construcao.html, seções 06 e 07.
--
-- QUEM RECEBE, conferido na produção em 26/09/2026 (contas × nichos, só leitura):
--   conta 33 · Pablo Thyago G. Dias / PX2 Empreendimentos · Lago da Pedra-MA
--
-- PRA QUEM: dono e gestor.
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('obras-fotos-da-obra', 'novidade', 'construcao', '{dono,gestor}',
 'As fotos da obra, guardadas por etapa',
 'Mande a foto da obra no WhatsApp dizendo a obra e a etapa: ela fica guardada na ficha, separada por etapa. Na reforma, o cliente vê as fotos no link do orçamento.',
 '/painel/obras',
 $txt$Foto de obra no celular se perde entre as fotos da família. Agora ela fica na obra.

PELO WHATSAPP

Mande a foto e diga de qual obra é e o que terminou: "terminou o telhado da casa 3". O assistente guarda a foto na etapa certa e marca a etapa como concluída. Foto de obra não vira lançamento — nota e cupom continuam indo pro caixa como sempre.

NA FICHA DA OBRA

Em Obras, cada ficha mostra as fotos separadas por etapa, com a data. Dá pra mandar foto pelo painel também, e apagar a que saiu errada.

AS FOTOS SÃO SÓ DE QUEM PODE VER

Elas ficam guardadas em lugar fechado: só abre quem está logado na empresa. Na reforma, depois do aceite, o cliente vê as fotos da obra dele no link do orçamento — e no Reforma Casa Brasil é com elas que ele pede à Caixa os 10% finais do crédito.$txt$,
 timestamptz '2026-09-26 18:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'obras-fotos-da-obra';
