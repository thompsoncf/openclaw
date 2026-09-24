-- 333_novidade_conversao_do_vendedor.sql
-- A conversão do vendedor (Placar, a tela de cada vendedor, o Raio-X do próprio
-- vendedor) e a do time (Visão) mudaram de régua. Mockup aprovado pelo dono em
-- 24/09/2026 (docs/mockups/conversao_do_vendedor.html).
--
-- O QUE MUDOU:
--   * conversão = fechados ÷ leads recebidos no período. Era fechados ÷ (fechados
--     + perdidos): o lead em aberto não entrava, e o número media quanto cada um
--     marca como perdido. Na Prime, em setembro, três vendedores com 3 contratos
--     em ~73 leads apareciam com 13%, 60% e 75%; agora os três ficam em 4%;
--   * as datas: a venda conta no mês em que entrou no fechamento (histórico do
--     funil) e a perda no mês em que foi perdida — não mais na última alteração;
--   * a tela do vendedor ganhou "O mês dele": recebidos, fechados, perdidos e
--     quantos dos recebidos seguem em aberto.
--
-- PRA QUEM: dono e gestor (Placar e Visão) e VENDEDOR — o número "Conversão" do
-- Raio-X dele muda de valor, e aviso de número que muda sem explicação vira
-- desconfiança. Público `servico`: é onde o app da Equipe tem vendedor.
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('conversao-do-vendedor', 'novidade', 'servico', '{dono,gestor,vendedor}',
 'A conversão agora é de cada 100 leads, quantos fecham',
 'A conversão do vendedor e do time passou a ser fechados sobre leads recebidos, contada pela data real da venda e da perda.',
 '/cockpit/equipe/placar',
 $txt$A conversão mudou de conta, e o número caiu pra quase todo mundo. Não foi a venda que caiu: foi a régua que ficou certa.

COMO ERA

Fechados divididos por fechados mais perdidos. O lead que ainda estava em aberto não entrava na conta. Quem marcava muito lead como perdido parecia converter pouco; quem deixava lead parado parecia converter muito, mesmo vendendo igual.

COMO FICOU

Fechados divididos pelos leads recebidos no período. É a resposta direta pra "de cada 100 leads que chegam, quantos viram contrato".

E as datas passaram a ser as de verdade: a venda conta no mês em que o lead entrou no fechamento, e a perda no mês em que foi perdida. Antes, qualquer alteração no lead — uma edição na ficha, a automação do funil — mudava o mês em que ele contava.

O MÊS DE CADA VENDEDOR

Na tela de cada vendedor, no Placar, entrou o bloco "O mês dele": leads recebidos, fechados, perdidos e quantos dos recebidos ainda estão em aberto. É ali que aparece quem fecha o ciclo e quem deixa lead parado.

No seu Raio-X, vendedor, a conversão mostra embaixo de quantos leads ela sai.$txt$,
 timestamptz '2026-09-24 18:10:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'conversao-do-vendedor';
