-- 352_novidade_plano_materiais_utensilios.sql
-- O aviso da 351: a conta 5.1.12 Materiais e Utensílios entrou no plano de contas,
-- em Despesas Operacionais. Pedido da Prime (25/09/2026), mas o plano é global e a
-- conta aparece ligada pra todas as empresas — por isso o público é `todos`, igual
-- ao aviso da 337 (Fardamentos), e o texto diz como desligar pra quem não usa.
--
-- O CORPO GASTA MAIS LINHAS NA FRONTEIRA DO QUE NO ANÚNCIO, de propósito. A conta
-- nasce entre três vizinhas (3.1.03 Insumos, 5.1.04 Escritório, 5.1.05 Limpeza), e
-- a medição em produção mostrou que a confusão já existe: uma conta lança bandeja
-- decorativa e toalha de mesa (é desta conta) e outra lança saco de delivery e
-- bandeja laminada (é embalagem, segue em Insumos). Aviso que só dá o nome da
-- conta faria o gasto se espalhar pelas quatro.
--
-- PRA QUEM: dono e gestor, que são quem lança despesa e cuida do plano. O vendedor
-- não vê plano de contas.
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('plano-materiais-utensilios', 'novidade', 'todos', '{dono,gestor}',
 'Nova conta no plano: Materiais e Utensílios',
 'O plano de contas ganhou Materiais e Utensílios, em Despesas Operacionais, pra lançar talher, louça, bandeja, toalha e utensílio de operação.',
 '/painel/empresa#plano-contas',
 $txt$O plano de contas ganhou a conta 5.1.12 Materiais e Utensílios, dentro de Despesas Operacionais.

É pra o que a casa usa e reusa pra trabalhar: talher, prato, taça, bandeja, toalha de mesa, utensílio de cozinha, instrumento, ferramenta pequena.

ONDE ELA NÃO É

A conta tem três vizinhas parecidas, e a diferença muda o seu resultado:

• Insumos e Materiais (Custos) — o que é consumido no que você vende e sai junto com o produto: comida, embalagem, saco de delivery, pote, copo descartável. Isso continua em Custos. Se você jogar embalagem aqui, sua margem por venda fica errada.
• Materiais de Escritório — papel, caneta, tinta de impressora.
• Material de Limpeza — detergente, pano, desinfetante.

A regra curta: se sai com o produto, é Custo. Se fica na casa, é Materiais e Utensílios.

Ela já vem ligada e aparece na lista quando você lança uma despesa. Se a sua empresa não usa, dá pra desligar em Empresa, no plano de contas. Lançamento antigo não muda de lugar sozinho — se quiser reclassificar algum, é na tela do lançamento.$txt$,
 timestamptz '2026-09-25 20:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'plano-materiais-utensilios';
