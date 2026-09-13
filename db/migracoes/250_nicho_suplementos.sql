-- 250_nicho_suplementos.sql
-- Nicho 'suplementos' (loja de suplemento COM COZINHA): MISTO — vende produto
-- (pote de whey, barra, vitamina) e serviço (o PLANO de marmitas, que é receita
-- recorrente). Casa o slug com finance/nichos.py e com o ramo Suplementos de
-- finance/cnpj_info.py. contas.nicho_id é FK -> nichos, então precisa existir
-- na tabela.
--
-- POR QUE AGORA: a conta 16 (SUPER FIT, Teresina-PI) está em 'alimentacao', que
-- é o nicho de LANCHONETE — fala em prato, porção e combo. Ela vende whey, café
-- proteico, vitamina em cápsula e prato feito. Medido no catálogo dela em
-- 13/09/2026: das 42 linhas, 12 estão sem categoria nenhuma e 10 caíram em
-- 'fruta' (categoria do HORTIFRÚTI) porque o sabor do produto é morango, pêssego
-- e manga, creme de coco, maracujá. A mesma categoria aparece em duas grafias
-- ("whey protein isolado hidrolizado" e "WHEY PROTEIN ISOLADO HIDROLISADO").
-- Nenhum relatório por categoria fecha assim.
--
-- O QUE ESTE NICHO TEM DE DIFERENTE, e está escrito na persona: ela TRANSFORMA
-- matéria-prima. O frango e a batata-doce que ela compra não vão pra prateleira
-- — viram prato. Por isso a persona separa INSUMO de MERCADORIA e manda o gasto
-- da cozinha pras categorias 'Insumos'/'Embalagens' (que entram neste mesmo PR),
-- e não pra 'Mercado', que é compra de casa.
--
-- E A MARMITA DELE É FRESCA, FEITA NA HORA (confirmado pelo dono em 13/09/2026):
-- validade curta, estoque de prato é do DIA e não acumula. É o que diferencia
-- esta cozinha de uma de congelados, onde o lote vive semanas.
--
-- MODO DO ORÇAMENTO: nada a fazer. Fora de `NICHOS_EVENTO` (finance/vendas.py) o
-- modo é 'recorrente', que é o certo — plano de marmita renova, não é festa com
-- sinal e data segurada.
--
-- ATENÇÃO AO PERFIL DO RAIO-X: por ser misto (vende_servico=true), esta conta
-- passa a cair no perfil 'recorrente' de finance/raio_x_perfil.py — e ganha
-- funil, Raio-X e follow-up no menu, que é o que serve pra vender plano em
-- academia e empresa. O perfil lê o SLUG do nicho; se a conta tiver
-- contas.vende_servico = false gravado, a aba Serviços continua fechada até ela
-- ligar em /painel/empresa.
--
-- 'tipo' na tabela é legado (a verdade é o par de flags no código), e vai
-- 'produto' como em 'eventos', que também é misto.
--
-- Aditiva e idempotente.

insert into nichos (nome, slug, tipo)
select 'Suplementos / Nutrição esportiva', 'suplementos', 'produto'
where not exists (select 1 from nichos n where n.slug = 'suplementos');

-- rollback:
--   delete from nichos where slug = 'suplementos';
