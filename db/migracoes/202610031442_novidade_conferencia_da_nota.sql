-- 202610031442_novidade_conferencia_da_nota.sql
-- O QUE FAZ: o aviso da conferência da nota no CD (finance/obra_conferencia.py),
--   pela seção 5 do CLAUDE.md. Portão `construcao` (350); dono e gestor (199).
-- POR QUÊ: PR 3a do CD das obras, aprovado pelo dono em 03/10/2026.
--
-- Aditiva e idempotente (if not exists / on conflict do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('obras-conferencia-nota', 'novidade', 'construcao', '{dono,gestor}',
 'A nota diz 60, chegaram 58: agora fica registrado',
 'Quando o caminhão descarrega no depósito, um toque confere a nota: bateu, ou digita quanto chegou. O estoque fica com o que chegou de verdade, e a diferença fica contra o fornecedor.',
 '/painel/obras/deposito?aba=entradas',
 $txt$O material que some antes de chegar no estoque é o mais difícil de achar — porque ninguém conferiu.

CONFERIR

Em Depósito (CD) › Entradas, as notas que chegaram no depósito aparecem em "Falta conferir", com cada item da nota. Bateu? Um toque. Não bateu? Corrija a quantidade do que chegou e confira.

O QUE MUDA NO ESTOQUE

O depósito fica com o que chegou de verdade, não com o que a nota dizia. Se a nota mudar de obra depois, vai junto o que chegou.

O FORNECEDOR

A diferença fica guardada contra o fornecedor: "Constrular — 2 notas com diferença nos últimos 30 dias". É o número que você leva pra conversa com ele. Errou a conferência? Dá pra desfazer.$txt$,
 timestamptz '2026-10-03 23:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'obras-conferencia-nota';
