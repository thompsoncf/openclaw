-- 156_conversas_indice_num8.sql
-- Índice que faltava pro webhook de contatos do WhatsApp QR não derrubar o app.
--
-- O /webhooks/wa-qr/contatos casa o contato da agenda com a conversa pelos 8
-- últimos dígitos do telefone (os 8 finais porque o WhatsApp devolve o número
-- ora com o 9 na frente, ora sem, ora com DDI — os 8 finais é a parte que não
-- muda). O casamento é feito assim:
--
--     right(regexp_replace(contato_ref, '\D', '', 'g'), 8) = %s
--
-- Isso é uma EXPRESSÃO sobre a coluna, não a coluna crua: nenhum dos índices
-- que já existiam em conversas (idx_conversas_conta, idx_conversas_conta_data,
-- idx_conversas_criado_em) serve. O planner só tinha uma saída — varrer a
-- tabela inteira. Uma varredura POR CONTATO.
--
-- O estrago apareceu em 15/08/2026: uma conta pareou o WhatsApp às 13:48 e o
-- Baileys despejou a agenda inteira do vendedor logo em seguida, em lotes de
-- 200 contatos. Entre 13:50 e 13:59 o serviço wa-qr registrou 2.446 respostas
-- 502 do app — cada requisição levava centenas de varreduras de conversas, o
-- app não vencia a fila e o proxy do Render passou a cortar. O painel ficou
-- lento junto, pra todo mundo.
--
-- A expressão do índice tem que ser IDÊNTICA à da consulta (inclusive o '\D' e
-- o 'g'), senão o planner não reconhece e continua varrendo. Se algum dia a
-- consulta em painel_prospeccao.py mudar, este índice tem que mudar junto.
--
-- Índice comum e não CONCURRENTLY de propósito: aplicar_migracoes roda tudo
-- dentro de uma transação (com statement_timeout desligado), e CREATE INDEX
-- CONCURRENTLY não pode rodar em transação. conversas é pequena o bastante
-- pro lock momentâneo de escrita não incomodar.
--
-- Aditivo e idempotente.

create index if not exists idx_conversas_num8
    on public.conversas (
        conta_id,
        canal,
        (right(regexp_replace(contato_ref, '\D', '', 'g'), 8))
    );

-- rollback:
--   drop index if exists public.idx_conversas_num8;
