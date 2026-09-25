-- 354_novidade_venda_da_casa.sql
-- O aviso da venda da casa e dos papéis dela, seguindo a seção 5 do CLAUDE.md: PR
-- que muda tela leva o aviso, no mesmo PR. Precisa da 353 (venda e documentos) e
-- da 350 (o portão `construcao`). Desenho aprovado pelo dono em 25/09/2026:
-- docs/mockups/nicho_construcao.html, seções 06 e 10.
--
-- PORTÃO `construcao`: a ficha da casa só abre pro perfil `obras`.
--
-- QUEM RECEBE, conferido na produção em 25/09/2026 (contas × nichos, só leitura):
--   conta 33 · Pablo Thyago G. Dias / PX2 Empreendimentos · Lago da Pedra-MA
--
-- PRA QUEM: dono e gestor.
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('obras-caminho-do-dinheiro', 'novidade', 'construcao', '{dono,gestor}',
 'O caminho do dinheiro de cada casa',
 'A ficha de cada casa mostra o que falta pro dinheiro da Caixa cair — habite-se, CND da obra, averbação, aprovação, assinatura e registro —, com os papéis, os prazos e a venda.',
 '/painel/obras',
 $txt$Na casa pronta vendida pela Caixa, o dinheiro do financiamento, do FGTS e do subsídio só cai depois que o contrato é registrado no cartório. E antes disso a casa precisa de habite-se, CND da obra e averbação na matrícula. Faltando um papel, o dinheiro fica parado na casa pronta.

O CAMINHO DO DINHEIRO

Na ficha de cada casa, em Obras, os passos aparecem em fila: obra pronta, habite-se, CND da obra, averbação, comprador aprovado, avaliação da Caixa, assinatura, registro e crédito. O primeiro que falta fica aceso — é o que trava aquela casa agora. Na lista de obras, cada casa pronta mostra o que a trava, e no topo aparece quanto dinheiro já foi gasto em casa que a Caixa ainda não pagou.

OS PAPÉIS E OS PRAZOS

Alvará, ART/RRT, CNO, habite-se, CND da obra, averbação, matrícula e as certidões da empresa, cada um com situação, número e datas. O Zaq avisa quando o prazo aperta: o CNO tem que ser feito em até 30 dias do início da obra, as certidões da empresa valem 180 dias, a avaliação da Caixa vale 12 meses, e o registro tem 30 dias depois da assinatura.

A VENDA

Cadastre o comprador, a faixa do Minha Casa Minha Vida e os valores da simulação: preço, financiamento, subsídio e FGTS. A entrada sai da conta sozinha. Se a avaliação da Caixa vier abaixo do preço, o alerta mostra quanto o comprador vai ter que cobrir. Quando o contrato é assinado, nascem as duas contas a receber: a entrada, do comprador, e o repasse, da Caixa — as duas na casa certa, então quando o dinheiro cai ele aparece na ficha dela.

PELO WHATSAPP

"Saiu o habite-se da casa 2", "assinou o contrato da casa 2", "caiu o dinheiro da casa 1" — o assistente marca o papel e anda a venda. E, no máximo uma vez por semana, lembra o que está travando cada casa pronta.

O que vem depois: a reforma, com orçamento de obra, contrato de empreitada e cobrança por etapa.$txt$,
 timestamptz '2026-09-25 23:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'obras-caminho-do-dinheiro';
