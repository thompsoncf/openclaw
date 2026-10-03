-- 671_novidade_deposito_cd.sql
-- O aviso da aba "Depósito (CD)" (web/painel_deposito.py), seguindo a seção 5 do
-- CLAUDE.md. Desenho aprovado pelo dono em 03/10/2026. Precisa da 350 (o portão
-- `construcao`).
--
-- PRA QUEM: dono e gestor (o pra_quem só aceita dono/gestor/vendedor — 199).
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('obras-deposito-cd', 'novidade', 'construcao', '{dono,gestor}',
 'O depósito virou o CD das obras, com aba própria',
 'O mestre pede o material pelo celular, o depósito separa e despacha, e a obra confirma o "recebi" com foto. Na aba Depósito (CD): o quadro dos pedidos, quanto dura cada material, o dinheiro parado no galpão e a sobra das casas prontas.',
 '/painel/obras/deposito',
 $txt$O galpão agora é o CD (centro de distribuição) das obras — e ganhou aba no menu: "Depósito (CD)".

O PEDIDO DA OBRA

O mestre pede pelo app: "20 sacos de cimento pra Casa 5, urgente". O pedido cai no quadro do CD: Pedido → Separando → Saiu → Recebido. Ninguém precisa aprovar: o CD separa direto, e você vê e pode cancelar.

O "RECEBI" É A PROVA

Quando o caminhão chega, o mestre confere no app, tira a foto e toca em "Recebi". É isso que tira o material do CD e põe na casa. Faltou algo? Ele diz quanto chegou, e a diferença fica no pedido.

O QUE A ABA MOSTRA

Quantos itens tem no CD, o dinheiro parado no galpão (pelo preço da nota), o que está abaixo do mínimo e quantos dias dura cada material pelo consumo das obras. E as casas prontas que ainda têm material: um toque devolve a sobra pro CD.

QUEM CUIDA DO GALPÃO

Tem alguém no depósito? Em Equipe, dê a ele o papel "Almoxarife": ele vê só a aba do CD — nada de caixa nem custo das obras.$txt$,
 timestamptz '2026-10-03 21:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'obras-deposito-cd';
