-- 252_novidade_categorias_insumo_embalagem.sql
-- O aviso das categorias de despesa 'Insumos' e 'Embalagens' (finance/models.py),
-- seguindo a seção 5 do CLAUDE.md. Precisa da 199 (pra_quem, resumo, link).
--
-- O PÚBLICO É 'todos', e aqui isso não é preguiça: a lista de categorias de
-- despesa é a MESMA pra toda conta do sistema. Quem abrir a tela de lançamento
-- amanhã vê duas opções novas no select, independentemente de ramo. Mirar em
-- 'suplementos' seria calar sobre uma tela que mudou pra todo mundo — o erro que
-- a seção 5 nomeia (17 dias sem aviso, em quatro entregas que mudaram a tela de
-- todos).
--
-- QUEM RECEBE, conferido na produção em 13/09/2026: as 22 contas vivas (trial ou
-- ativa) — 22 pessoas entre donos e gestores. É a lista inteira de propósito.
--
-- POR QUE AS CATEGORIAS NASCERAM (o detalhe está no comentário de
-- CATEGORIAS_DESPESA): as seis compras de insumo da cozinha da SUPER FIT
-- (conta 16) estavam em "Mercado", que é a categoria de compra de supermercado
-- DOMÉSTICA. Matéria-prima virava despesa de casa, e o CMV — matéria-prima mais
-- embalagem sobre o preço de venda — não tinha como existir.
--
-- PRA QUEM: dono e gestor. O vendedor não lança despesa da empresa.
--
-- Aditiva e idempotente (on conflict (chave) do nothing). Não mexe em check
-- nenhum: 'todos' já existe desde a 174.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('categorias-insumos-embalagens', 'novidade', 'todos', '{dono,gestor}',
 'Duas categorias novas de despesa: Insumos e Embalagens',
 'Quem transforma matéria-prima — cozinha, padaria, confeitaria — agora separa o custo do produto da despesa do dia a dia, e passa a enxergar o CMV.',
 '/painel/financeiro',
 $txt$A lista de categorias de despesa nasceu pensando em pessoa física: Mercado, Moradia, Contas de casa, Lazer, Pet. Funciona pra quem só gasta — e atrapalha quem PRODUZ.

O caso que fez isso aparecer: uma cozinha lançando carne, queijo, pães e compras de atacado. Tudo caía em "Mercado", misturado com a compra que a pessoa faz pra casa. Ou seja: a matéria-prima do que ela vende estava contada como despesa doméstica.

Agora existem duas categorias novas:

INSUMOS — o que entra pra virar produto. Farinha, carne, legume, tempero, embalagem de vácuo, o queijo da nota do laticínio.

EMBALAGENS — marmitex, pote, sacola, etiqueta, filme. Separado de propósito, porque em cozinha a embalagem pesa no custo de cada unidade e some quando fica junto do resto.

POR QUE ISSO IMPORTA: matéria-prima mais embalagem, dividido pelo preço de venda, é o CMV — o número que diz se cada prato, bolo ou cesta dá lucro. Com tudo em "Mercado", esse número não existia. Com as duas categorias separadas, ele aparece sozinho no relatório de despesas.

Se você já lançou insumo como "Mercado", não precisa refazer nada: é só trocar a categoria dos lançamentos antigos quando passar por eles. O relatório recalcula sozinho.

Quem não transforma nada — só revende, ou só presta serviço — pode ignorar: são duas opções a mais no select, e nada muda no seu dia.$txt$,
 timestamptz '2026-09-13 15:10:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'categorias-insumos-embalagens';
