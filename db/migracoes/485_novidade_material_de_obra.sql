-- 485_novidade_material_de_obra.sql
-- O aviso do controle de material (finance/obra_material.py), seguindo a seção 5
-- do CLAUDE.md. Desenho aprovado pelo dono em 02/10/2026 (PR 2 do mapa). Precisa
-- da 350 (o portão `construcao`).
--
-- PRA QUEM: dono e gestor (o pra_quem só aceita dono/gestor/vendedor — 199).
--
-- Aditiva e idempotente (on conflict (chave) do nothing).

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values

('obras-material', 'novidade', 'construcao', '{dono,gestor}',
 'A nota agora vira material contado',
 'A mesma foto da nota que lança o dinheiro passa a guardar os itens: 60 sacos de cimento entram contados na obra. "Usei 15 sacos na casa 2" dá baixa, o depósito avisa quando baixar do mínimo, e o sistema aponta a casa que está gastando acima das irmãs da quadra.',
 '/painel/obras',
 $txt$Material é onde a obra sangra — e agora o Zaq conta ele junto com o dinheiro, sem nenhum trabalho a mais.

A NOTA QUE VOCÊ JÁ FOTOGRAFA

A foto da nota de material continua lançando o dinheiro na obra, como sempre. A novidade: os itens entram CONTADOS — "20 sacos de cimento CP-II", "30 barras de ferro 8 mm" — na obra da nota, ou no depósito se ainda não tem obra (escolhendo a obra no botão, o material vai junto). O custo não muda em nada.

O DIA A DIA É FALADO

"Usei 15 sacos na casa 2" dá baixa. "Levei 10 sacos do depósito pra quadra 5" transfere (e divide entre as casas). "Chegou 60 sacos" entra no depósito. "Quanto cimento tem na casa 3?" responde na hora. Apontar é opcional: quem aponta ganha o saldo fino; quem não aponta já ganha o alerta de compra.

OS ALERTAS QUE NINGUÉM CADASTROU

Num loteamento as casas são iguais — então a régua é a própria quadra: se uma casa está consumindo cimento 20% acima das irmãs na mesma altura, o Zaq avisa. Uso maior que entrada (furo) aparece na hora. E o depósito tem mínimo: baixou, avisou.

ONDE VER

Na ficha da obra (seção Material), no perfil do lote no mapa 3D, e no depósito, na tela de Obras — com o mínimo de cada material editável ali mesmo.$txt$,
 timestamptz '2026-10-02 20:00:00+00')

on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'obras-material';
