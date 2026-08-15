-- 148_servico_categoria_foto.sql
-- Categoria e foto no catálogo de serviços — o que o orçamento de evento
-- (migração 147) precisa pra ficar igual ao modelo aprovado:
--
--   • categoria: agrupa os itens e dá o SUBTOTAL POR CATEGORIA na folha
--     (Locação de espaço / Buffet / Locação de móveis e utensílios /
--     Serviços terceirizados), o mesmo vocabulário das receitas que a
--     migração 143 criou no plano de contas;
--   • foto_url: num evento o cliente compra o que vê — o item leva a foto do
--     espaço, do mobiliário ou da montagem. Mesma ideia (e mesmo upload) da
--     foto de produto: sobe do celular ou cola o link.
--
-- Aditivo e idempotente. Serviço sem categoria/foto continua funcionando —
-- a folha só não mostra o que não existe.

alter table public.servicos_catalogo add column if not exists categoria text;
alter table public.servicos_catalogo add column if not exists foto_url  text;

-- rollback:
--   alter table public.servicos_catalogo drop column if exists categoria,
--     drop column if exists foto_url;
