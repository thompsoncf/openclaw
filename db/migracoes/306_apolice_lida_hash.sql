-- 306_apolice_lida_hash.sql
-- A MESMA APÓLICE MANDADA DUAS VEZES NÃO VIRA DUAS.
--
-- O PEDIDO (dono, 21/09/2026): "faz a checagem de duplicidade caso mande o mesma
-- apolice nao salvar e avisar".
--
-- POR QUE O `mensagem_id` NÃO BASTA. Ele já impede reler a MESMA MENSAGEM (o
-- wa-qr reentrega), e é um índice parcial desde a 305. Mas as três portas de
-- conversa gravam `mensagem_id` NULO — no assistente e no Telegram não existe
-- linha em `mensagens` —, então reenviar o mesmo arquivo criava uma linha nova a
-- cada vez. Medido na Liberal no mesmo dia: o corretor mandou o PDF da Mapfre
-- três vezes testando, e ficaram três pré-cadastros do mesmo documento na fila de
-- conferência. Conferir em duplicata é o caminho mais curto pra cadastrar em
-- duplicata.
--
-- A CHAVE É O CONTEÚDO, não o nome do arquivo: o WhatsApp não manda nome nenhum
-- pelo assistente (vira "apolice-recebida.pdf") e o corretor renomeia à vontade.
-- sha256 dos bytes é o que não muda.
--
-- ÍNDICE PARCIAL, e não `unique` seco: linha antiga não tem hash, e duas leituras
-- que FALHARAM (sem PDF guardado) não colidem entre si. Só o que foi lido de
-- verdade entra na trava.
--
-- Aditivo e idempotente. Não preenche o hash do que já existe: são três linhas na
-- Liberal, e recalcular exigiria baixar o PDF do cofre numa migração — DDL que
-- fala com a rede é o tipo de coisa que trava a tabela viva por minutos.

alter table public.apolice_lida
    add column if not exists pdf_hash text;

create unique index if not exists ux_apolice_lida_hash
    on public.apolice_lida (conta_id, pdf_hash)
 where pdf_hash is not null;

-- rollback:
--   drop index if exists public.ux_apolice_lida_hash;
--   alter table public.apolice_lida drop column if exists pdf_hash;
