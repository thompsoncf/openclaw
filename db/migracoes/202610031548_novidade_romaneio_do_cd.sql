-- 202610031548_novidade_romaneio_do_cd.sql
-- O QUE FAZ: o aviso do romaneio e do "onde" no CD (finance/obra_romaneio.py),
--   pela seção 5 do CLAUDE.md. Portão `construcao` (350); dono e gestor (199).
-- POR QUÊ: PR 3c do CD das obras, aprovado pelo dono em 03/10/2026.
--
-- Aditiva e idempotente (on conflict do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('obras-romaneio-cd', 'novidade', 'construcao', '{dono,gestor}',
 'O caminhão sai com romaneio: imprime ou manda pro motorista',
 'Marque os pedidos que vão no caminhão e toque em “Saiu”: nasce o romaneio da viagem — o que vai pra cada casa —, pronto pra imprimir ou mandar pelo WhatsApp. E cada material ganha o seu lugar no depósito.',
 '/painel/obras/deposito?aba=saidas',
 $txt$Cada viagem do caminhão agora é um romaneio.

MONTAR A VIAGEM

Em Depósito (CD) › Pedidos das obras, marque os pedidos que vão juntos, escreva o motorista (e o WhatsApp dele, se quiser) e toque em "Saiu — montar romaneio".

O ROMANEIO

Em Saídas e romaneio, cada viagem mostra o que vai pra cada casa e se o mestre já confirmou o "recebi" com a foto. Imprima, ou mande pro motorista pelo WhatsApp com um toque.

ONDE FICA CADA MATERIAL

Na aba Estoque, cada material ganha o seu lugar no depósito (baia 1, prateleira A2, pátio). Ele aparece no pedido, pra quem separa, no romaneio e na contagem do dia.$txt$,
 timestamptz '2026-10-04 01:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'obras-romaneio-cd';
