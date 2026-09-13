-- 253_nicho_suplementos_nome.sql
-- O nicho 'suplementos' passa a se chamar "Suplementos e cozinha fit".
--
-- POR QUE RENOMEAR, decisão do dono em 13/09/2026: "Nutrição esportiva" descreve a
-- PRATELEIRA e esconde a COZINHA — e na conta que deu origem ao nicho (16, SUPER
-- FIT) a cozinha não é o detalhe: dos R$ 3.084,78 que passaram pela conta,
-- R$ 1.312,22 são insumo de cozinha e R$ 0,00 são de suplemento. O rótulo é o que
-- a pessoa lê no select de ramo em /painel/empresa e no cabeçalho da tela de
-- Produtos; se ele não nomeia a cozinha, quem tem loja COM cozinha não se
-- reconhece na lista.
--
-- SÃO DOIS LUGARES, e é por isso que esta migração existe além do label no
-- código: o painel do cliente monta o select com `nichos.lista_nichos()`, que lê o
-- CÓDIGO (web/portal), enquanto o admin lista `select id, nome from nichos`, que lê
-- a TABELA (web/admin). Trocar só um faz o admin e o painel chamarem o mesmo ramo
-- por nomes diferentes. `tests/test_nicho_suplementos.py` compara os dois.
--
-- POR QUE UMA MIGRAÇÃO NOVA, e não editar a 250: a 250 já entrou na main (#684) e
-- migração mesclada não se reescreve — quem já aplicou ficaria com um histórico
-- diferente do arquivo. O caminho é sempre o update por cima, e ele é barato
-- porque `slug` é a chave estável: quem manda no código é o slug, e nada aponta
-- pro texto de `nome`.
--
-- O AVISO TAMBÉM CITA O NOME (a 251 escreve 'Agora existe o ramo "Suplementos /
-- Nutrição esportiva"'), e por isso ele é corrigido aqui junto. Na prática as três
-- migrações rodam no MESMO deploy: a 251 insere o texto antigo e esta o corrige
-- em seguida, sem ninguém ler no meio. Se um deploy tiver entregue a 251 sozinha,
-- o update abaixo continua sendo o conserto certo — e ninguém tinha lido o aviso
-- (novidade_lida da conta 16 estava em 0 na medição de 13/09).
--
-- Aditiva e idempotente: rodar de novo escreve o mesmo texto.

-- ────────────────────────────────────────────── 1. o nome do ramo
update nichos set nome = 'Suplementos e cozinha fit'
 where slug = 'suplementos';

-- ────────────────────────────────────────────── 2. o aviso que cita o nome
update public.novidades
   set corpo = replace(corpo,
        'Agora existe o ramo "Suplementos / Nutrição esportiva"',
        'Agora existe o ramo "Suplementos e cozinha fit"')
 where chave = 'nicho-loja-de-suplementos';

-- rollback:
--   update nichos set nome = 'Suplementos / Nutrição esportiva' where slug = 'suplementos';
--   update public.novidades set corpo = replace(corpo,
--       'Agora existe o ramo "Suplementos e cozinha fit"',
--       'Agora existe o ramo "Suplementos / Nutrição esportiva"')
--    where chave = 'nicho-loja-de-suplementos';
