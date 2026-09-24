-- 329_novidade_visao_da_equipe.sql
-- A aba Visão do app da Equipe: o funil inteiro, o "Período", e o que a gestão de
-- tráfego pergunta. Mockup aprovado pelo dono em 24/09/2026
-- (docs/mockups/visao_da_equipe.html).
--
-- O QUE MUDOU, em /cockpit (dono e gestor):
--   * FUNIL com as MESMAS colunas do quadro do painel — faltavam o fim (na Prime,
--     CONTRATO ASSINADO e Perdido). R$ só onde há orçamento ou contrato: nas etapas
--     de prospecção, só a quantidade.
--   * A pílula PERÍODO ao lado de Hoje · Semana · Mês, com atalhos e duas datas.
--     Depois de escolhida ela mostra o recorte ("10–23 set").
--   * LEADS POR DIA, com o dia da semana e a data em cada barra; 1ª resposta e quem
--     nunca foi respondido.
--   * QUANDO CHEGAM: por dia da semana e turno, e quem chega fora do expediente e
--     quanto espera. Na Prime: 25% fora do horário, esperando 7h40.
--   * O QUE PEDEM, no vocabulário do nicho: tipo de festa, mês e convidados pra quem
--     vende festa; segmento e porte pra quem vende serviço.
--   * POR QUE PERDEMOS, lido das conversas (migração 328, selo 💬 lido): o motivo do
--     vendedor vale mais; onde ele não marcou, vale o que a conversa diz.
--
-- PRA QUEM: dono e gestor — são quem abre a Visão (e a gestão de tráfego, que
-- entra como gestor). Vendedor não tem essa tela.
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('visao-da-equipe', 'novidade', 'servico', '{dono,gestor}',
 'A Visão da Equipe mostra de onde vem o lead e por que ele não fecha',
 'O funil do app ganhou as mesmas colunas do painel, a Visão ganhou o filtro Período, e quatro blocos novos: leads por dia, quando chegam, o que pedem e por que perdemos — este lido das próprias conversas.',
 '/cockpit',
 $txt$A aba Visão do app da Equipe ficou do tamanho da pergunta de quem gerencia — e de quem cuida do anúncio.

O FUNIL INTEIRO

As mesmas colunas do quadro do painel, na mesma ordem. Faltavam as do fim: o contrato assinado e o perdido. O perdido aparece apagado — quem olha o funil precisa ver o vazamento. E o valor em reais só aparece onde há orçamento ou contrato de verdade; nas etapas de prospecção, só a quantidade.

O PERÍODO QUE VOCÊ QUISER

Ao lado de Hoje · Semana · Mês, a pílula Período: atalhos (ontem, 7 dias, 30 dias, mês passado) ou as duas datas. Depois de escolhido, ela mostra o recorte — "10–23 set" — e a escolha fica guardada quando você volta pra tela.

LEADS POR DIA

Uma barra por dia, com o número em cima e o dia da semana e a data embaixo. O fim de semana fica num tom mais apagado — é ele que costuma explicar os buracos. Embaixo: a média por dia, a 1ª resposta e quem nunca foi respondido.

QUANDO CHEGAM

Por dia da semana e por turno — e quantos chegam fora do expediente e quanto esperam pela primeira resposta. Anúncio que traz gente às 22h e é respondido às 9h está pagando pra esperar.

O QUE PEDEM

No vocabulário do seu ramo: pra quem vende festa, o tipo, o mês e o tamanho; pra quem vende serviço, o segmento e o porte da empresa.

POR QUE PERDEMOS, LIDO DAS CONVERSAS

O motivo que o vendedor marca continua valendo mais. Onde ele não marcou nada, ou marcou "Outro", o sistema lê o fim da conversa e anota o motivo com o selo 💬 lido, usando a mesma lista de motivos da sua empresa. E separa quem nem era cliente — currículo, fornecedor, pedido de doação —, que é o número que a gestão de tráfego mais precisa pra ajustar o público. Ninguém vê o texto da conversa nesse bloco: só as contagens.$txt$,
 timestamptz '2026-09-24 18:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'visao-da-equipe';
