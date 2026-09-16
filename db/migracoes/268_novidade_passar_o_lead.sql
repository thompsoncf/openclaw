-- 268_novidade_passar_o_lead.sql
-- O vendedor passa o lead pro colega, e toda troca passa a deixar rastro.
--
-- O PEDIDO (dono, 16/09/2026): "preciso que com o próprio vendedor mude de quem é
-- quem, porque existem leads que já foram atendidos no passado e eles precisam se
-- entender". E, ao aprovar o mockup: "passa direto e o gestor também passa".
--
-- O QUE MUDOU NA TELA
-- 1) /cockpit — na folha de ações do lead nasce "Passar pra outro vendedor": um
--    seletor com os colegas, um campo de motivo (opcional) e o botão. O lead sai
--    da Fila de quem passou e entra na de quem recebeu.
-- 2) /cockpit — quem RECEBE vê "Fulano te passou a Lêda" em cima da Fila, com o
--    motivo escrito. O aviso sai sozinho quando ele abre o lead; não tem ✕ porque
--    ir ver é a própria ação que o aviso pede.
-- 3) /cockpit — no aviso de "esse número já fala com outro vendedor", que já
--    existia, entra o atalho que faltava: "Passar este lead pra Fulano", um toque.
--    Antes o aviso dizia o problema e parava aí, e os dois iam resolver no
--    WhatsApp e pedir pro dono apertar o botão.
-- 4) /cockpit — a ficha do cliente ganha "Quem já atendeu": as trocas deste lead
--    e os OUTROS leads do mesmo número, com quem atendeu e quando.
-- 5) /painel/prospeccao — o GESTOR passa a atribuir e reatribuir alvo, o que
--    antes era só do dono.
--
-- A REGRA: O VENDEDOR DÁ, E NUNCA PEGA. Passar o próprio lead é abrir mão do que é
-- seu. Puxar o do colega mexe no que é do outro e abre a porta pro roubo de lead,
-- que num time comissionado é briga na certa. Dono e gestor passam nos dois
-- sentidos — é deles o desempate quando os vendedores não se entendem.
--
-- O QUE FALTAVA POR BAIXO. A troca não deixava rastro nenhum:
-- `prospeccao.vendedor_id` era sobrescrito e nenhuma tabela guardava quem tinha
-- antes (`funil_movimentos` registra ETAPA, não dono). Sem histórico ninguém prova
-- quem atendeu primeiro — que é justamente o "se entender" do pedido. A tabela
-- `lead_repasse` (migração 267) passa a guardar toda troca, inclusive as do dono e
-- as do gestor: guardar só as do vendedor deixaria o histórico mentindo por omissão
-- exatamente nos casos em que alguém questiona a decisão.
--
-- COMO O PROBLEMA NASCE, e por que "quem já atendeu" olha o NÚMERO e não só a
-- tabela nova: `distribuicao.atribuir_se_sem_dono` nunca rouba lead que já tem
-- dono, mas quando o mesmo número escreve de novo nasce um lead NOVO, sem dono, e o
-- rodízio entrega pro próximo da fila. O contato é o mesmo; a linha na tabela, não.
-- Caso real na conta 34: Lêda Lopes, 20/08 com a Jacqueline e 24/08 com o Thiago,
-- mesmo número — dois leads, nenhum repasse entre eles. Sem a leitura por número o
-- bloco novo nasceria vazio justamente nos casos que motivaram o pedido. São 7
-- números repetidos na conta 34 e 5 na conta 3, destes 4 em vendedores diferentes.
--
-- O PORTÃO: `servico`. Repasse é sobre carteira e vendedor — existe em qualquer
-- nicho com funil, e não existe em conta de produto, que não tem vendedor dono de
-- lead. Medido nas duas pontas (§6): a conta 34 (eventos) tem 4 vendedores ativos
-- mais o dono, e é onde a queixa nasceu; a conta 3 (consultoria) tem 3 vendedores e
-- é dela que vêm os 4 números repetidos em vendedores diferentes. Nenhum vocabulário
-- de nicho entra na tela — "lead", "colega" e "vendedor" valem nos dois.
--
-- O PEDAÇO DO GESTOR alcança hoje UMA conta: a 35 é a única com gestor ativo em
-- produção. Entra assim mesmo porque o portão é o papel, não a conta — quem
-- contratar um gestor amanhã já encontra a tela pronta.
--
-- PRA QUEM: vendedor (ganhou o botão e o aviso), gestor (ganhou a atribuição no
-- painel) e dono (a troca dele passa a virar histórico).
--
-- Aditivo e idempotente.

insert into public.novidades (chave, tipo, publico, pra_quem, titulo, resumo, link, corpo, publicado_em) values
('passar-o-lead-pro-colega', 'novidade', 'servico', '{dono,gestor,vendedor}',
 'Agora o vendedor passa o lead pro colega — e fica registrado',
 'Quem está atendendo pode passar o lead direto pra outro vendedor, com o motivo, e toda troca passa a ficar no histórico do cliente.',
 '/cockpit',
 $txt$Quando o cliente já tinha falado com outro vendedor, resolver isso dava três passos: descobrir no WhatsApp com quem ele falou, combinar entre vocês e pedir pro dono trocar no painel. Agora é um toque.

NO LEAD, no botão de ações, tem "Passar pra outro vendedor". Você escolhe o colega, escreve por quê (é opcional, mas ajuda muito quem recebe) e pronto: o lead sai da sua Fila e entra na dele.

VOCÊ PASSA O QUE É SEU. Não dá pra puxar o lead de um colega — quem tem é quem passa. Se o lead é do outro e você acha que devia ser seu, fale com ele ou com o gestor: gestor e dono trocam nos dois sentidos.

QUEM RECEBE É AVISADO. Aparece um aviso em cima da Fila — "Fulano te passou a Lêda" — com o motivo que você escreveu. Ele some sozinho quando o colega abre o lead. Ninguém mais vai descobrir um lead novo na carteira sem saber de onde veio.

NAQUELE AVISO de "esse número também fala com outro vendedor", que já aparecia na conversa, agora tem o botão pronto: "Passar este lead pra Fulano". Um toque, e acabou.

NA FICHA DO CLIENTE tem um bloco novo, "Quem já atendeu". Ele mostra as trocas deste lead e também os OUTROS atendimentos do mesmo número — porque quando o cliente volta a escrever depois de um tempo o sistema abre um lead novo, e antes não havia como saber que ele já tinha conversado com alguém da equipe. É o que serve pra vocês se entenderem sem ter que perguntar pro dono quem falou primeiro.

E NO PAINEL, o gestor agora também atribui e reatribui alvo — antes só o dono fazia isso.$txt$,
 timestamptz '2026-09-16 21:00:00+00')
on conflict (chave) do nothing;

-- rollback:
--   delete from public.novidades where chave = 'passar-o-lead-pro-colega';
