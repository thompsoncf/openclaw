-- 153_servico_icone.sql
-- Ícone do serviço no lugar da foto.
--
-- Serviço não é produto: não tem embalagem pra fotografar. A foto (148) custava
-- rede, sumia na impressão sem fundo e, no fim, metade dos itens ficava SEM foto
-- (ninguém cadastra foto de "DJ" ou de "mão de obra de entradas") — e a linha do
-- orçamento desalinhava. O ícone é de traço, imprime nítido em qualquer tamanho
-- e sai igual em fotocópia P&B.
--
-- Guarda só a CHAVE (ex.: 'som', 'cozinha'); o desenho mora no código
-- (finance/icones_servico.py). Vazio não é problema: o ícone é deduzido do nome
-- e da categoria do serviço, então nenhum item fica sem selo.
--
-- `foto_url` fica onde está, sem tocar: catálogo antigo não perde nada e a
-- coluna segue servindo se um dia a empresa quiser foto de novo.
--
-- Aditivo e idempotente.

alter table public.servicos_catalogo add column if not exists icone text;

-- rollback:
--   alter table public.servicos_catalogo drop column if exists icone;
